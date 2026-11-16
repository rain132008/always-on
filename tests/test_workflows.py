from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic, sleep, time
from unittest.mock import Mock
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
import yaml

from backend.api import create_app
from backend.connectors.git import ReviewComment
from backend.connectors.mock_git import MockGit
from backend.contracts import RunResult
from backend.database import Database
from backend.git_workflows import register_git
from backend.outbox import Outbox, mr_review_key
from backend.tasks import Tasks
from backend.workflow import ConnectorRegistry

ROOT = Path(__file__).resolve().parents[1]


def wait_for(read, ready, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        last = read()
        if ready(last):
            return last
        sleep(0.08)
    pytest.fail(f"等待工作流状态超时：{last}")


def detail(client, task_id):
    return client.get(f"/api/tasks/{task_id}").json()


def group(client, workflow_id):
    return [task for task in client.get("/api/tasks").json() if task["workflow_id"] == workflow_id]


def test_mr_reuses_session_and_posts_one_comment_per_sha(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/mock/git/mr_opened", json={}).status_code == 202
        read_comments = lambda: client.get("/api/mock/git/comments").json()
        first_comment = wait_for(read_comments, lambda comments: len(comments) == 1)[0]
        tasks = group(client, "mr-review")
        assert len(tasks) == 1
        task_id = tasks[0]["id"]
        first = detail(client, task_id)
        session = first["session"]["external_id"]
        assert session and first["status"] == "completed"
        assert first["artifacts"][0]["content"] == first_comment["comment"]

    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/mock/git/mr_opened", json={}).status_code == 202
        assert client.post("/api/mock/git/mr_updated", json={}).status_code == 202
        comments = wait_for(read_comments, lambda records: len(records) == 2)
        assert comments[0]["comment"]["head_sha"] != comments[1]["comment"]["head_sha"]
        assert len({item["business_key"] for item in comments}) == 2
        assert client.post("/api/mock/git/mr_updated", json={}).status_code == 202
        assert client.post("/api/workflows/mr-review/scan", json={}).json()["seen"] == 0
        second = detail(client, task_id)
        assert len(group(client, "mr-review")) == 1
        assert [run["resume_id"] for run in second["runs"]] == [None, session]
        assert second["session"]["external_id"] == session
        assert len(second["events"]) == len(second["artifacts"]) == len(read_comments()) == 2


def test_git_outbox_reconciles_published_comment_and_skips_stale_head(tmp_path):
    database = Database(tmp_path / "core.sqlite3")
    database.migrate()
    git = MockGit(tmp_path / "git.sqlite3", ROOT / "fixtures" / "mock-git.json")
    registry = ConnectorRegistry()
    register_git(registry, "mock-git", git)
    outbox = Outbox(database, registry.deliveries)
    git.advance("mr_opened")
    ref = git.discover_mr_events(None).items[0].ref
    mr = git.get_mr(ref)
    content = ReviewComment(ref=ref, head_sha=mr.head_sha, summary="样例检查", findings=[])
    key = mr_review_key(ref.provider, ref.project_id, ref.iid, mr.head_sha, "v1")
    stale_key = mr_review_key(ref.provider, ref.project_id, ref.iid, mr.head_sha, "v2")
    with database.write() as connection:
        outbox.enqueue(connection, "mock-git", "mr_review", key, content.model_dump(mode="json"))
    claimed = outbox.claim()
    remote_id = git.publish_comment(claimed["business_key"], content)
    git.advance("mr_updated")
    outbox.recover_interrupted()
    outbox.tick()
    assert outbox.list()[0]["remote_id"] == remote_id
    with database.write() as connection:
        outbox.enqueue(connection, "mock-git", "mr_review", stale_key, content.model_dump(mode="json"))
    outbox.tick()
    actions = {row["business_key"]: row["status"] for row in outbox.list()}
    assert actions == {key: "sent", stale_key: "superseded"}
    assert len(git.comments()) == 1
    database.engine.dispose()


def test_new_subject_revision_supersedes_active_result_and_interrupted_retry(tmp_path):
    database = Database(tmp_path / "core.sqlite3")
    database.migrate()
    actions = Mock()
    tasks = Tasks(database, tmp_path, tmp_path, outbox=None, result_actions=actions)

    def enqueue(head):
        with database.write() as connection:
            return tasks.submit(connection, "MR 样例", head, "mr_head", head, time(),
                                workflow={"id": "review"}, subject="mr-1")

    task_id = enqueue("head-1")
    first = tasks.claim(30)
    assert enqueue("head-2") == task_id
    session_id = uuid4()
    assert tasks.finish(first, RunResult(
        contract_version="1", task_id=task_id, run_id=first["run_id"], generation=first["generation"],
        session_id=session_id, outcome="completed", summary="过期报告",
    ))
    actions.apply.assert_not_called()
    interrupted = tasks.claim(30)
    assert interrupted["summary"] == "head-2" and interrupted["resume_id"] == str(session_id)
    tasks.require_recovery(interrupted, "测试中断")
    enqueue("head-3")
    tasks.recover(interrupted, retry=True)
    newest = tasks.claim(30)
    assert newest["summary"] == "head-3" and newest["resume_id"] == str(session_id)
    tasks.finish(newest, error="结束测试")
    assert tasks.claim(30) is None
    assert [run["status"] for run in tasks.detail(task_id)["runs"]] == ["superseded", "interrupted", "failed"]
    database.engine.dispose()


def test_document_cron_commits_cursor_and_resumes_clarification(tmp_path, monkeypatch):
    tz = ZoneInfo("Asia/Shanghai")
    slot = (datetime.now(tz) + timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
    with TestClient(create_app(tmp_path)) as client:
        wait_for(lambda: client.get("/api/workflows").json(),
                 lambda items: any(item["id"] == "doc-maintenance" and item["last_slot"] is not None for item in items))
        assert client.post("/api/mock/git/merged_batch", json={}).status_code == 202
        runtime = client.app.state.runtime
        engine = runtime.workflows
        original = runtime.tasks.submit

        def lose_batch(connection, title, prompt, *args, **kwargs):
            if title.startswith("sample-web"):
                raise RuntimeError("模拟第二仓任务入库中断")
            return original(connection, title, prompt, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(runtime.tasks, "submit", lose_batch)
            with pytest.raises(RuntimeError, match="模拟第二仓"):
                engine.scan("doc-maintenance", slot=slot.timestamp())
        assert group(client, "doc-maintenance") == []
        assert next(item for item in engine.list() if item["id"] == "doc-maintenance")["cursors"] == {}

        engine.tick(slot.timestamp() + 30)
        tasks = wait_for(lambda: group(client, "doc-maintenance"),
                         lambda items: len(items) == 2 and all(item["status"] == "waiting_human" for item in items))
        config = next(item for item in engine.list() if item["id"] == "doc-maintenance")
        assert config["last_slot"] == slot.timestamp()
        assert set(config["cursors"].values()) == {"1", "2"}
        saved = detail(client, next(item["id"] for item in tasks if item["title"].startswith("sample-api")))

    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/workflows/doc-maintenance/scan", json={}).json()["seen"] == 0
        task_id = saved["id"]
        request_id = saved["requests"][0]["id"]
        text = "只更新访客访问的使用说明"
        assert client.post(f"/api/requests/{request_id}/reply", json={"answer": text}).status_code == 202
        result = wait_for(lambda: detail(client, task_id), lambda item: item["status"] == "completed")
        assert result["runs"][1]["resume_id"] == saved["session"]["external_id"]
        assert result["artifacts"][0]["kind"] == "document_suggestion"
        assert text in result["artifacts"][0]["content"]["suggestion"]
        assert client.post("/api/mock/git/merged_increment", json={}).status_code == 202
        second = client.post("/api/workflows/doc-maintenance/scan", json={}).json()
        assert second["seen"] == 1 and set(second["cursors"].values()) == {"2", "3"}
        assert len(group(client, "doc-maintenance")) == 3


def test_yaml_adds_workflow_using_existing_connector(tmp_path, monkeypatch):
    configs = tmp_path / "workflows"
    configs.mkdir()
    for name in ("sdd-task.yaml", "doc-maintenance.yaml"):
        data = yaml.safe_load((ROOT / "workflows" / name).read_text(encoding="utf-8"))
        if name == "doc-maintenance.yaml":
            data["id"] = "sample-web-only"
            data["source"]["options"]["repositories"] = [{"project_id": "sample-web", "branch": "main"}]
        (configs / name).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("ALWAYS_ON_WORKFLOWS_DIR", str(configs))
    with TestClient(create_app(tmp_path / "runtime")) as client:
        wait_for(lambda: client.get("/api/workflows").json(),
                 lambda items: any(item["id"] == "sample-web-only" and item["last_slot"] is not None for item in items))
        assert client.post("/api/mock/git/merged_batch", json={}).status_code == 202
        scan = client.post("/api/workflows/sample-web-only/scan", json={})
        assert scan.status_code == 200 and scan.json()["seen"] == 1
        assert len(group(client, "sample-web-only")) == 1
