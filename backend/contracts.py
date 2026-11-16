from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, field_validator, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)]
MIN_WAKEUP_SECONDS = 5
MAX_WAKEUP_SECONDS = 7 * 24 * 60 * 60
MAX_WAKEUP_CHAIN = 5
WakeupSeconds = Annotated[int, Field(strict=True, ge=MIN_WAKEUP_SECONDS, le=MAX_WAKEUP_SECONDS)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskInput(Contract):
    title: ShortText
    prompt: LongText


class ReplyInput(Contract):
    answer: LongText


class SessionName(Contract):
    name: ShortText


class SessionResume(Contract):
    instruction: LongText


class TaskClose(Contract):
    reason: ShortText | None = None


class WorkflowControl(Contract):
    enabled: bool = Field(strict=True)


class AgentSettings(Contract):
    name: ShortText
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)] = ""
    prompt: LongText


class WorkflowAgentSelection(Contract):
    agent_id: ShortText


class RecoveryInput(Contract):
    operation: Literal["stop", "retry"]


class ReconciliationInput(Contract):
    decision: Literal["delivered", "absent"]
    evidence: LongText
    remote_id: ShortText | None = None

    @model_validator(mode="after")
    def check_remote_id(self):
        if self.decision == "delivered" and self.remote_id is None:
            raise ValueError("确认远端已送达时需要提供远端记录 ID")
        return self


class TaskContext(Contract):
    contract_version: Literal["1"] = "1"
    task_id: UUID
    run_id: UUID
    generation: int = Field(ge=1)
    resume_id: UUID | None
    wake_reason: ShortText
    title: ShortText
    prompt: LongText
    input_summary: LongText
    workflow: dict | None = None
    scheduled_wakeups_left: int = Field(default=MAX_WAKEUP_CHAIN, ge=0, le=MAX_WAKEUP_CHAIN)


class RunResult(Contract):
    contract_version: Literal["1"]
    task_id: UUID
    run_id: UUID
    generation: int = Field(ge=1)
    session_id: UUID
    outcome: Literal["completed", "needs_human", "schedule_wakeup", "failed"]
    summary: LongText
    question: LongText | None = None
    after_seconds: WakeupSeconds | None = Field(default=None, validate_default=True)

    @field_validator("after_seconds")
    @classmethod
    def check_delay(cls, value, info: ValidationInfo):
        if (info.data.get("outcome") == "schedule_wakeup") != (value is not None):
            raise ValueError("只有预约唤醒的结果必须附带延期秒数")
        return value

    @model_validator(mode="after")
    def check_question(self):
        if (self.outcome == "needs_human") != (self.question is not None):
            raise ValueError("只有等待人工的结果必须附带问题")
        return self


class WorkflowRunResult(RunResult):
    output: dict | None = None
