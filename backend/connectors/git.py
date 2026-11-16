from dataclasses import dataclass
from typing import Annotated, Generic, Literal, Protocol, TypeVar

from pydantic import Field, StringConstraints

from ..contracts import Contract, LongText, ShortText
from ..outbox import Reconciled

DiffText = Annotated[str, StringConstraints(max_length=120000)]

class MRRef(Contract):
    provider: ShortText
    project_id: ShortText
    iid: int = Field(gt=0)


class MergeRequest(Contract):
    ref: MRRef
    title: ShortText
    head_sha: ShortText
    status: Literal["open", "closed", "merged"]


class MREvent(Contract):
    ref: MRRef
    kind: Literal["opened", "updated", "closed", "merged"]


class MRDiff(Contract):
    head_sha: ShortText
    text: DiffText


class Repository(Contract):
    project_id: ShortText
    branch: ShortText


class MergedCommit(Contract):
    repository: Repository
    sha: ShortText
    summary: ShortText
    diff: DiffText


class ReviewInput(Contract):
    mr: MergeRequest
    diff: MRDiff


class Finding(Contract):
    path: ShortText
    detail: LongText


class ReviewOutput(Contract):
    reviewed_sha: ShortText
    findings: list[Finding] = Field(max_length=20)


class ReviewComment(Contract):
    ref: MRRef
    head_sha: ShortText
    summary: LongText
    findings: list[Finding] = Field(max_length=20)


class DocumentSuggestion(Contract):
    commit_sha: ShortText
    document: ShortText
    suggestion: LongText


Item = TypeVar("Item")


@dataclass(frozen=True)
class Page(Generic[Item]):
    items: list[Item]
    cursor: str | None


class GitConnector(Protocol):
    def discover_mr_events(self, after: str | None) -> Page[MREvent]: ...

    def get_mr(self, ref: MRRef) -> MergeRequest: ...

    def get_diff(self, ref: MRRef, head_sha: str) -> MRDiff: ...

    def merged_commits(self, repository: Repository, after: str | None) -> Page[MergedCommit]: ...

    def publish_comment(self, key: str, comment: ReviewComment) -> str: ...

    def reconcile_comment(self, key: str, comment: ReviewComment) -> Reconciled: ...
