import os
import logging
from pathlib import Path
from threading import Event, Thread

from filelock import FileLock, Timeout

from .agent import ClaudeAgent, MockAgent
from .agent_catalog import AgentCatalog
from .connectors.mock import MockRemote
from .connectors.mock_git import MockGit
from .database import Database
from .git_workflows import GitResultActions, register_git
from .outbox import Outbox
from .runner import Runner
from .tasks import Tasks
from .workflow import ConnectorRegistry, ResultActions, WorkflowEngine

logger = logging.getLogger(__name__)
PROJECT = Path(__file__).resolve().parent.parent


class Runtime:
    def __init__(self, data, workspace, claude_smoke=False, *, sdd_runner="mock"):
        self.data = data.resolve()
        self.workspace = workspace.absolute()
        self.lock = FileLock(self.data / "service.lock")
        self.stop = Event()
        self.claude_smoke = claude_smoke
        if sdd_runner not in {"mock", "claude"}:
            raise ValueError("ALWAYS_ON_SDD_RUNNER 只能设为 mock 或 claude。")
        self.sdd_runner = "claude-smoke" if claude_smoke else sdd_runner
        self.legacy_runner = "claude-smoke" if claude_smoke else "mock"
        self.mode = "mixed" if self.sdd_runner == "claude" else self.sdd_runner

    def __enter__(self):
        self.data.mkdir(parents=True, exist_ok=True)
        try:
            self.lock.acquire(timeout=0)
        except Timeout as error:
            raise RuntimeError("运行目录已有一个 Always-on 服务。") from error

        database = Database(self.data / "always-on.sqlite3")
        self.database = database
        try:
            database.migrate()
            self.agents = AgentCatalog(database)
            log_root = self.data / "logs"
            log_root.mkdir(exist_ok=True)
            registry = ConnectorRegistry()
            registry.register_delivery("mock-notify", MockRemote(self.data / "mock-remote.sqlite3"))
            self.mock_git = None
            if not self.claude_smoke:
                self.mock_git = MockGit(self.data / "mock-git.sqlite3", PROJECT / "fixtures" / "mock-git.json")
                register_git(registry, "mock-git", self.mock_git)
            self.outbox = Outbox(database, registry.deliveries)
            git_actions = GitResultActions(self.outbox)
            actions = ResultActions({
                "publish_review": git_actions.publish_review,
                "save_document_suggestion": git_actions.save_document_suggestion,
            })
            self.tasks = Tasks(database, self.workspace, log_root, self.outbox, actions)
            configs = Path(os.environ.get("ALWAYS_ON_WORKFLOWS_DIR", PROJECT / "workflows"))
            self.workflows = WorkflowEngine(
                database, self.tasks, registry, actions, configs, self.agents, manual_only=self.claude_smoke,
                sdd_runner=self.sdd_runner,
            )
            agents = [] if self.claude_smoke else [MockAgent()]
            if self.sdd_runner != "mock":
                agents.append(ClaudeAgent(self.data / "claude-config", smoke=self.claude_smoke))
            self.runner = Runner(self.tasks, self.stop, agents, refresh_subject=self.workflows.refresh_subject)
            self.runner.check_interrupted()
            self.outbox.recover_interrupted()
            self.worker = Thread(target=self.work, name="always-on-dispatcher")
            self.worker.start()
        except BaseException:
            database.engine.dispose()
            self.lock.release()
            raise
        return self

    def __exit__(self, kind, value, traceback):
        self.stop.set()
        self.worker.join()
        self.database.engine.dispose()
        self.lock.release()

    def work(self):
        while not self.stop.is_set():
            self.workflows.tick()
            run = None
            try:
                run = self.tasks.claim(self.runner.timeout, legacy_runner=self.legacy_runner)
                if run is not None:
                    self.runner.run(run)
            except Exception:
                logger.exception("Agent 调度失败")
                if run is not None:
                    try:
                        self.tasks.require_recovery(run, "执行中断，请检查进程和工作区后手动恢复。")
                    except Exception:
                        logger.exception("无法写入 Run 恢复状态")

            if self.stop.is_set():
                break
            try:
                self.outbox.tick()
            except Exception:
                logger.exception("外部动作处理失败")
                try:
                    self.outbox.recover_interrupted()
                except Exception:
                    logger.exception("无法写入外部动作核对状态")
            self.stop.wait(0.2)
