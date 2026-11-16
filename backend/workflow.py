import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from time import time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import yaml
from croniter import croniter
from pydantic import Field, field_validator

from .agent_catalog import BUILTIN_AGENTS
from .contracts import Contract, ShortText
from .database import execute, new_id, one, rows
from .tasks import workflow_enabled
from .workspace import Conflict, Missing

logger = logging.getLogger(__name__)

class SourceReadError(Exception):
    """允许显示在工作流历史中的数据源错误。"""


def scan_failure_reason(error):
    if isinstance(error, SourceReadError):
        return str(error)
    return f"扫描失败（{type(error).__name__}）。请查看服务日志，游标保留在原位置。"


class Manual(Contract):
    type: Literal["manual"]


class Poll(Contract):
    type: Literal["poll"]
    seconds: int = Field(ge=1)


class Cron(Contract):
    type: Literal["cron"]
    expression: ShortText
    timezone: ShortText

    @field_validator("expression")
    @classmethod
    def valid_cron(cls, value):
        if len(value.split()) != 5 or not croniter.is_valid(value):
            raise ValueError("使用五段 Cron 表达式。")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as error:
            raise ValueError("使用有效的 IANA 时区。") from error
        return value


class Source(Contract):
    connector: ShortText
    operation: ShortText
    options: dict = Field(default_factory=dict)


class Skill(Contract):
    id: ShortText
    version: ShortText


class AgentBinding(Contract):
    profile: ShortText
    skill: Skill


class Workflow(Contract):
    id: ShortText
    name: ShortText
    trigger: Annotated[Manual | Poll | Cron, Field(discriminator="type")]
    source: Source | None = None
    condition: Literal["always", "new_item"]
    agent: AgentBinding
    session: Literal["per_task", "per_subject"]
    result: dict[Literal["completed", "needs_human", "failed"], list[ShortText]]

    def snapshot(self, agent, payload=None, *, runner="mock"):
        config = self.model_dump(mode="json")
        encoded = json.dumps({"workflow": config, "agent": agent, "runner": runner},
                             sort_keys=True, ensure_ascii=False).encode("utf-8")
        return {
            "id": self.id, "version": hashlib.sha256(encoded).hexdigest(),
            "profile": self.agent.profile, "skill": self.agent.skill.model_dump(),
            "agent": agent, "runner": runner,
            "connector": self.source.connector if self.source else None,
            "result": self.result, "input": payload,
        }


@dataclass(frozen=True)
class SourceItem:
    key: str
    subject: str
    kind: str
    title: str
    prompt: str
    payload: dict


@dataclass(frozen=True)
class SourceBatch:
    items: list[SourceItem]
    cursors: dict[str, str]


class ConnectorRegistry:
    def __init__(self):
        self.sources = {}
        self.deliveries = {}

    def register_source(self, connector, operation, options, read):
        key = (connector, operation)
        if key in self.sources:
            raise ValueError(f"重复的数据源 {key}")
        self.sources[key] = (options, read)

    def register_delivery(self, connector, delivery):
        if connector in self.deliveries:
            raise ValueError(f"重复的外部动作 Connector {connector}")
        self.deliveries[connector] = delivery

    def bind(self, source):
        key = (source.connector, source.operation)
        if key not in self.sources:
            raise ValueError(f"未注册的数据源 {key}")
        model, read = self.sources[key]
        return model.model_validate(source.options), read


class ResultActions:
    def __init__(self, handlers):
        self.handlers = handlers

    def apply(self, connection, run, result, now):
        for name in run["workflow"]["result"].get(result.outcome, ()):
            if name not in self.handlers:
                raise ValueError("持久化工作流引用了未注册的结果动作。")
            self.handlers[name](connection, run, result, now)

    @staticmethod
    def artifact(connection, run, kind, content, now):
        execute(connection, """
            INSERT INTO task_artifacts (id, task_id, run_id, kind, content, created_at)
            VALUES (:id, :task, :run, :kind, :content, :now)
        """, id=new_id(), task=run["task_id"], run=run["run_id"], kind=kind,
            content=content.model_dump_json(), now=now)


class WorkflowEngine:
    def __init__(self, database, tasks, registry, actions, directory: Path, agents, *, manual_only=False,
                 sdd_runner="mock"):
        self.db = database
        self.tasks = tasks
        self.agents = agents
        self.sdd_runner = sdd_runner
        self.lock = Lock()
        self.retry_after = {}
        self.workflows = {}
        self.configs = {}
        self.sources = {}
        paths = [directory / "sdd-task.yaml"] if manual_only else sorted(directory.glob("*.yaml"))
        for path in paths:
            try:
                config = path.read_text(encoding="utf-8")
                workflow = Workflow.model_validate(yaml.safe_load(config))
                if workflow.id in self.workflows:
                    raise ValueError("工作流 ID 重复。")
                if workflow.agent.profile not in BUILTIN_AGENTS:
                    raise ValueError("Agent Profile 未注册。")
                for names in workflow.result.values():
                    if len(names) != len(set(names)) or any(name not in actions.handlers for name in names):
                        raise ValueError("结果动作重复或未注册。")
                if isinstance(workflow.trigger, Manual):
                    if workflow.source or workflow.condition != "always" or workflow.session != "per_task":
                        raise ValueError("手动工作流使用 always、per_task，且不能配置数据源。")
                elif workflow.source is None or workflow.condition != "new_item":
                    raise ValueError("扫描工作流需要已注册的数据源和 new_item 条件。")
                else:
                    self.sources[workflow.id] = registry.bind(workflow.source)
                self.workflows[workflow.id] = workflow
                self.configs[workflow.id] = config
            except (OSError, ValueError, yaml.YAMLError) as error:
                raise ValueError(f"工作流配置 {path.name} 无效：{error}") from error
        if "sdd-task" not in self.workflows or not isinstance(self.workflows["sdd-task"].trigger, Manual):
            raise ValueError("必须配置手动工作流 sdd-task。")

    @staticmethod
    def slot(trigger, now):
        if isinstance(trigger, Poll):
            return now // trigger.seconds * trigger.seconds
        local = datetime.fromtimestamp(now, ZoneInfo(trigger.timezone)) + timedelta(microseconds=1)
        return croniter(trigger.expression, local).get_prev(datetime).timestamp()

    def tick(self, now=None):
        now = time() if now is None else now
        for workflow_id in self.sources:
            if now < self.retry_after.get(workflow_id, 0):
                continue
            try:
                slot = self.slot(self.workflows[workflow_id].trigger, now)
                self.scan(workflow_id, slot=slot, trigger="scheduled")
            except Exception:
                self.retry_after[workflow_id] = now + 5
                logger.exception("扫描工作流 %s 失败，保留原游标", workflow_id)

    def refresh_subject(self, run):
        workflow = run.get("workflow")
        if run.get("subject_key") and workflow and workflow["id"] in self.sources:
            self.scan(workflow["id"], trigger="result_refresh")

    def scan(self, workflow_id, *, slot=None, trigger="manual"):
        if workflow_id not in self.sources:
            raise Missing("未找到可扫描的工作流。")
        workflow = self.workflows[workflow_id]
        options, read = self.sources[workflow_id]
        with self.lock:
            with self.db.engine.connect() as connection:
                if not workflow_enabled(connection, workflow_id):
                    if trigger == "manual":
                        raise Conflict("工作流已停用，请启用后再扫描。")
                    return None
                previous = one(connection, "SELECT slot FROM workflow_ticks WHERE workflow_id = :id", id=workflow_id)
                if slot is not None and previous and previous["slot"] >= slot:
                    return None
                cursors = {row["stream"]: row["cursor"] for row in rows(connection, """
                    SELECT stream, cursor FROM workflow_cursors WHERE workflow_id = :id
                """, id=workflow_id)}
            started = time()
            try:
                batch = read(options, cursors)
                now = time()
                with self.db.write() as connection:
                    # 读取 Connector 期间可以停用。提交批次前再次核对，保证启停与入库有清晰顺序。
                    if not workflow_enabled(connection, workflow_id):
                        return {"seen": 0, "cursors": cursors}
                    self.commit_batch(connection, workflow, batch, slot, now)
                    if batch.items or trigger == "manual":
                        self.record_scan(connection, workflow_id, trigger, "completed", started, seen=len(batch.items))
                return {"seen": len(batch.items), "cursors": cursors | batch.cursors}
            except Exception as error:
                if trigger == "manual":
                    logger.exception("手动扫描工作流 %s 失败", workflow_id)
                with self.db.write() as connection:
                    self.record_scan(connection, workflow_id, trigger, "failed", started,
                                     reason=scan_failure_reason(error))
                raise

    def commit_batch(self, connection, workflow, batch, slot, now):
        agent = self.agents.effective(connection, workflow.id, workflow.agent.profile) if batch.items else None
        for item in batch.items:
            key = json.dumps([workflow.id, workflow.source.connector, item.key], ensure_ascii=False)
            self.tasks.submit(
                connection, item.title, item.prompt, item.kind, key, now,
                workflow=workflow.snapshot(agent, item.payload),
                subject=item.subject if workflow.session == "per_subject" else None,
            )
        for stream, cursor in batch.cursors.items():
            execute(connection, """
                INSERT INTO workflow_cursors (workflow_id, stream, cursor) VALUES (:id, :stream, :cursor)
                ON CONFLICT (workflow_id, stream) DO UPDATE SET cursor = excluded.cursor
            """, id=workflow.id, stream=stream, cursor=cursor)
        if slot is not None:
            execute(connection, """
                INSERT INTO workflow_ticks (workflow_id, slot) VALUES (:id, :slot)
                ON CONFLICT (workflow_id) DO UPDATE SET slot = excluded.slot
            """, id=workflow.id, slot=slot)

    @staticmethod
    def record_scan(connection, workflow_id, trigger, status, started, *, seen=None, reason=None):
        execute(connection, """
            INSERT INTO workflow_scans (workflow_id, trigger, status, seen, reason, started_at, finished_at)
            VALUES (:id, :trigger, :status, :seen, :reason, :started, :finished)
        """, id=workflow_id, trigger=trigger, status=status, seen=seen, reason=reason,
            started=started, finished=time())
        execute(connection, """
            DELETE FROM workflow_scans WHERE workflow_id = :id AND id NOT IN (
                SELECT id FROM workflow_scans WHERE workflow_id = :id ORDER BY id DESC LIMIT 100
            )
        """, id=workflow_id)

    def set_enabled(self, workflow_id, enabled):
        if workflow_id not in self.workflows:
            raise Missing("未找到该工作流。")
        with self.db.write() as connection:
            if workflow_enabled(connection, workflow_id) == enabled:
                return
            execute(connection, """
                INSERT INTO workflow_controls (workflow_id, enabled, updated_at) VALUES (:id, :enabled, :now)
                ON CONFLICT (workflow_id) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at
            """, id=workflow_id, enabled=int(enabled), now=time())
            if enabled:
                execute(connection, "DELETE FROM workflow_ticks WHERE workflow_id = :id", id=workflow_id)

    def manual_snapshot(self, connection):
        workflow = self.workflows["sdd-task"]
        return workflow.snapshot(self.agents.effective(connection, workflow.id, workflow.agent.profile),
                                 runner=self.sdd_runner)

    def runner_for(self, workflow_id):
        return self.sdd_runner if workflow_id == "sdd-task" else "mock"

    def set_agent(self, workflow_id, agent_id):
        if workflow_id not in self.workflows:
            raise Missing("未找到该工作流。")
        self.agents.bind(workflow_id, agent_id)

    def detail(self, workflow_id):
        if workflow_id not in self.workflows:
            raise Missing("未找到该工作流。")
        with self.db.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            runs = rows(connection, """
                SELECT r.id, r.status, r.summary, r.exit_code, r.generation, r.created_at, r.finished_at, r.agent_name,
                       r.runner, t.id AS task_id, t.title AS task_title
                FROM runs r JOIN tasks t ON t.id = r.task_id WHERE t.workflow_id = :id
                ORDER BY r.created_at DESC, r.generation DESC LIMIT 50
            """, id=workflow_id)
            scans = rows(connection, """
                SELECT id, trigger, status, seen, reason, started_at, finished_at
                FROM workflow_scans WHERE workflow_id = :id ORDER BY id DESC LIMIT 50
            """, id=workflow_id)
            enabled = workflow_enabled(connection, workflow_id)
            workflow = self.workflows[workflow_id]
            agent = self.agents.effective(connection, workflow_id, workflow.agent.profile)
        return workflow.model_dump(mode="json") | {
            "enabled": enabled, "yaml": self.configs[workflow_id], "runs": runs, "scans": scans,
            "runner": self.runner_for(workflow_id),
            "selected_agent": {"id": agent["id"], "name": agent["name"]},
        }

    def list(self):
        with self.db.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            checkpoints = rows(connection, "SELECT workflow_id, stream, cursor FROM workflow_cursors")
            ticks = {row["workflow_id"]: row["slot"] for row in rows(connection, "SELECT * FROM workflow_ticks")}
            controls = {row["workflow_id"]: bool(row["enabled"]) for row in rows(connection, "SELECT * FROM workflow_controls")}
            selected = {}
            for workflow in self.workflows.values():
                agent = self.agents.effective(connection, workflow.id, workflow.agent.profile)
                selected[workflow.id] = {"id": agent["id"], "name": agent["name"]}
        return [
            workflow.model_dump(mode="json") | {
                "last_slot": ticks.get(workflow.id),
                "enabled": controls.get(workflow.id, True),
                "runner": self.runner_for(workflow.id),
                "selected_agent": selected[workflow.id],
                "cursors": {item["stream"]: item["cursor"] for item in checkpoints if item["workflow_id"] == workflow.id},
            }
            for workflow in self.workflows.values()
        ]
