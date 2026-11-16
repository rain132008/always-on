import json
import sqlite3
from contextlib import closing, contextmanager

from ..database import new_id
from ..outbox import DeliveryObsolete
from .git import MergedCommit, MergeRequest, MRDiff, MREvent, Page
from .mock import MockRemote


class MockGit:
    def __init__(self, path, fixture):
        self.path = path
        self.scenarios = json.loads(fixture.read_text(encoding="utf-8"))
        self.remote = MockRemote(path)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS mock_mr (
                    seq INTEGER PRIMARY KEY, project TEXT NOT NULL, iid INTEGER NOT NULL,
                    event TEXT NOT NULL, snapshot TEXT NOT NULL, sha TEXT NOT NULL, diff TEXT
                );
                CREATE TABLE IF NOT EXISTS mock_commit (
                    seq INTEGER PRIMARY KEY, project TEXT NOT NULL, branch TEXT NOT NULL,
                    commit_json TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            yield connection

    def advance(self, name):
        scenario = self.scenarios[name]
        with self.connect() as connection:
            for record in scenario.get("mrs", []):
                mr = MergeRequest.model_validate(record["mr"])
                event = MREvent(ref=mr.ref, kind=record["event"])
                connection.execute("""
                    INSERT OR IGNORE INTO mock_mr VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (record["seq"], mr.ref.project_id, mr.ref.iid, event.model_dump_json(),
                      mr.model_dump_json(), mr.head_sha, record.get("diff")))
            for record in scenario.get("commits", []):
                commit = MergedCommit.model_validate(record["commit"])
                connection.execute("""
                    INSERT OR IGNORE INTO mock_commit VALUES (?, ?, ?, ?)
                """, (record["seq"], commit.repository.project_id, commit.repository.branch,
                      commit.model_dump_json()))

    def discover_mr_events(self, after):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT seq, event FROM mock_mr WHERE seq > ? ORDER BY seq", (int(after or 0),),
            ).fetchall()
        return self.page(rows, MREvent, after)

    def get_mr(self, ref):
        with self.connect() as connection:
            row = connection.execute("""
                SELECT snapshot FROM mock_mr WHERE project = ? AND iid = ? ORDER BY seq DESC LIMIT 1
            """, (ref.project_id, ref.iid)).fetchone()
        if row is None:
            raise LookupError("Mock MR 不存在。")
        mr = MergeRequest.model_validate_json(row[0])
        if mr.ref != ref:
            raise LookupError("Mock MR 来源不匹配。")
        return mr

    def get_diff(self, ref, head_sha):
        self.get_mr(ref)
        with self.connect() as connection:
            row = connection.execute("""
                SELECT diff FROM mock_mr WHERE project = ? AND iid = ? AND sha = ? AND diff IS NOT NULL
                ORDER BY seq DESC LIMIT 1
            """, (ref.project_id, ref.iid, head_sha)).fetchone()
        if row is None:
            raise LookupError("Mock Diff 不存在。")
        return MRDiff(head_sha=head_sha, text=row[0])

    def merged_commits(self, repository, after):
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT seq, commit_json FROM mock_commit
                WHERE project = ? AND branch = ? AND seq > ? ORDER BY seq
            """, (repository.project_id, repository.branch, int(after or 0))).fetchall()
        return self.page(rows, MergedCommit, after)

    @staticmethod
    def page(rows, model, after):
        return Page([model.model_validate_json(payload) for _seq, payload in rows],
                    str(rows[-1][0]) if rows else after)

    def publish_comment(self, key, comment):
        # 在同一 Mock 事务中核对当前 head 和写评论。
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = self.remote.lookup(connection, key)
            if saved:
                return saved[0]
            row = connection.execute("""
                SELECT snapshot FROM mock_mr WHERE project = ? AND iid = ? ORDER BY seq DESC LIMIT 1
            """, (comment.ref.project_id, comment.ref.iid)).fetchone()
            mr = MergeRequest.model_validate_json(row[0]) if row else None
            if mr is None or mr.ref != comment.ref or mr.status != "open" or mr.head_sha != comment.head_sha:
                raise DeliveryObsolete("MR 已关闭或 head 已更新，跳过旧评审评论。")
            remote_id = new_id()
            connection.execute("""
                INSERT INTO received VALUES (?, 'mr_review', ?, ?)
            """, (key, comment.model_dump_json(), remote_id))
            return remote_id

    def reconcile_comment(self, key, comment):
        return self.remote.reconcile("mr_review", key, comment.model_dump(mode="json"))

    def comments(self):
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT business_key, payload, remote_id FROM received WHERE kind = 'mr_review'
                ORDER BY rowid
            """).fetchall()
        return [{"business_key": key, "comment": json.loads(payload), "remote_id": remote}
                for key, payload, remote in rows]
