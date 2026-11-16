import json

from pydantic import Field

from .connectors.git import (
    DocumentSuggestion, GitConnector, MergedCommit, Repository, ReviewComment, ReviewInput, ReviewOutput,
)
from .contracts import Contract
from .outbox import mr_review_key
from .workflow import ResultActions, SourceBatch, SourceItem, SourceReadError


class MROptions(Contract):
    pass


class CommitOptions(Contract):
    repositories: list[Repository] = Field(min_length=1)


def identity(*parts):
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


class GitSources:
    def __init__(self, git: GitConnector):
        self.git = git

    def merge_requests(self, _options, cursors):
        try:
            page = self.git.discover_mr_events(cursors.get("events"))
        except Exception as error:
            raise SourceReadError(
                f"读取 MR 事件失败（{type(error).__name__}），游标保留在原位置。"
            ) from error
        if page.items and (page.cursor is None or page.cursor == cursors.get("events")):
            raise ValueError("MR 事件页面需要前进的游标。")
        refs = {identity(event.ref.provider, event.ref.project_id, event.ref.iid): event.ref for event in page.items}
        items = []
        for subject, ref in refs.items():
            try:
                mr = self.git.get_mr(ref)
                diff = self.git.get_diff(ref, mr.head_sha) if mr.status == "open" else None
            except Exception as error:
                raise SourceReadError(
                    f"读取 {ref.project_id} !{ref.iid} 的 MR 或 Diff 失败（{type(error).__name__}），游标保留在原位置。"
                ) from error
            if mr.ref != ref:
                raise ValueError("MR 结果与请求标识不匹配。")
            if diff is None:
                continue
            if diff.head_sha != mr.head_sha:
                raise ValueError("Diff 与 MR head 不匹配。")
            review = ReviewInput(mr=mr, diff=diff)
            items.append(SourceItem(
                identity(subject, mr.head_sha), subject, "mr_head",
                f"{ref.project_id} !{ref.iid} · {mr.title}"[:300],
                f"评审 {ref.project_id} !{ref.iid} 的 head {mr.head_sha}，输出可核实的发现。",
                review.model_dump(mode="json"),
            ))
        return SourceBatch(items, {"events": page.cursor} if page.cursor is not None else {})

    def commits(self, options, cursors):
        items, updated = [], {}
        for repo in options.repositories:
            stream = identity(repo.project_id, repo.branch)
            try:
                page = self.git.merged_commits(repo, cursors.get(stream))
            except Exception as error:
                raise SourceReadError(
                    f"读取 {repo.project_id}/{repo.branch} 的合并提交失败（{type(error).__name__}），游标保留在原位置。"
                ) from error
            if page.items and (page.cursor is None or page.cursor == cursors.get(stream)):
                raise ValueError("合并增量页面需要前进的游标。")
            for commit in page.items:
                if commit.repository != repo:
                    raise ValueError("合并提交与请求仓库或分支不匹配。")
                key = identity(stream, commit.sha)
                items.append(SourceItem(
                    key, key, "merged_commit", f"{repo.project_id} · {commit.summary}"[:300],
                    f"判断 {repo.project_id}/{repo.branch} 的 {commit.sha} 是否需要更新文档。",
                    commit.model_dump(mode="json"),
                ))
            if page.cursor is not None:
                updated[stream] = page.cursor
        return SourceBatch(items, updated)


class GitComments:
    def __init__(self, git: GitConnector):
        self.git = git

    def deliver(self, kind, key, payload):
        if kind != "mr_review":
            raise ValueError("Git 评论 Connector 只接受 mr_review。")
        return self.git.publish_comment(key, ReviewComment.model_validate(payload))

    def reconcile(self, kind, key, payload):
        if kind != "mr_review":
            raise ValueError("Git 评论 Connector 只接受 mr_review。")
        return self.git.reconcile_comment(key, ReviewComment.model_validate(payload))


def register_git(registry, name, git: GitConnector):
    sources = GitSources(git)
    registry.register_source(name, "merge_requests", MROptions, sources.merge_requests)
    registry.register_source(name, "merged_commits", CommitOptions, sources.commits)
    registry.register_delivery(name, GitComments(git))


class GitResultActions:
    def __init__(self, outbox):
        self.outbox = outbox

    def publish_review(self, connection, run, result, now):
        workflow = run["workflow"]
        review = ReviewInput.model_validate(workflow["input"])
        report = ReviewOutput.model_validate(getattr(result, "output", None))
        if report.reviewed_sha != review.mr.head_sha:
            raise ValueError("评审结果与输入 SHA 不匹配。")
        ref = review.mr.ref
        skill = workflow["skill"]
        key = mr_review_key(ref.provider, ref.project_id, ref.iid, report.reviewed_sha,
                            identity(skill["id"], skill["version"]))
        comment = ReviewComment(ref=ref, head_sha=report.reviewed_sha, summary=result.summary,
                                findings=report.findings)
        self.outbox.enqueue(connection, workflow["connector"], "mr_review", key, comment.model_dump(mode="json"))
        ResultActions.artifact(connection, run, "mr_review", comment, now)

    @staticmethod
    def save_document_suggestion(connection, run, result, now):
        commit = MergedCommit.model_validate(run["workflow"]["input"])
        suggestion = DocumentSuggestion.model_validate(getattr(result, "output", None))
        if suggestion.commit_sha != commit.sha:
            raise ValueError("文档建议与输入提交不匹配。")
        ResultActions.artifact(connection, run, "document_suggestion", suggestion, now)
