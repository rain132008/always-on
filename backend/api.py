import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .contracts import (
    AgentSettings, ReconciliationInput, RecoveryInput, ReplyInput, SessionName, SessionResume,
    TaskClose, TaskInput, WorkflowAgentSelection, WorkflowControl,
)
from .service import Runtime
from .workflow import scan_failure_reason
from .workspace import Conflict, Missing

PROJECT = Path(__file__).resolve().parent.parent


def create_app(data: Path | None = None, workspace: Path | None = None, *, claude_smoke=False, sdd_runner="mock"):
    data = data or Path(os.environ.get("ALWAYS_ON_DATA", PROJECT / "runtime-data"))
    if claude_smoke:
        data = data / "claude-smoke"
        workspace_config = None
        workspace = data / "synthetic-sdd"
    else:
        workspace_config = workspace or os.environ.get("ALWAYS_ON_SDD_ROOT")
        workspace = Path(workspace_config) if workspace_config else data / "mock-sdd"

    @asynccontextmanager
    async def lifespan(app):
        if not workspace_config:
            workspace.mkdir(parents=True, exist_ok=True)
        with Runtime(data, workspace, claude_smoke=claude_smoke, sdd_runner=sdd_runner) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title="SDD Always-on", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def verify_origin(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            same_origin = f"{request.url.scheme}://{request.headers.get('host')}"
            # Vite 的本地代理会保留浏览器 Origin。
            allowed_dev = {"http://localhost:5173", "http://127.0.0.1:5173"}
            if origin is not None and origin not in allowed_dev | {same_origin}:
                return JSONResponse({"detail": "请求来源与本地管理台不匹配。"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "此操作需要 JSON 请求。"}, status_code=415)
        return await call_next(request)

    @app.exception_handler(Conflict)
    async def on_conflict(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(Missing)
    async def on_missing(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.get("/api/health")
    def health(request: Request):
        runtime = request.app.state.runtime
        return {"mode": runtime.mode, "worker_alive": runtime.worker.is_alive()}

    @app.get("/api/tasks")
    def list_tasks(request: Request):
        return request.app.state.runtime.tasks.list()

    @app.post("/api/tasks", status_code=201)
    def create_task(body: TaskInput, request: Request):
        runtime = request.app.state.runtime
        return {"id": runtime.tasks.create(body.title, body.prompt, snapshot=runtime.workflows.manual_snapshot)}

    @app.get("/api/agents")
    def list_agents(request: Request):
        return request.app.state.runtime.agents.list()

    @app.post("/api/agents", status_code=201)
    def create_agent(body: AgentSettings, request: Request):
        return {"id": request.app.state.runtime.agents.save(body)}

    @app.post("/api/agents/{agent_id}")
    def edit_agent(agent_id: str, body: AgentSettings, request: Request):
        return {"id": request.app.state.runtime.agents.save(body, agent_id)}

    @app.get("/api/workflows")
    def list_workflows(request: Request):
        return request.app.state.runtime.workflows.list()

    @app.get("/api/workflows/{workflow_id}")
    def workflow_detail(workflow_id: str, request: Request):
        return request.app.state.runtime.workflows.detail(workflow_id)

    @app.post("/api/workflows/{workflow_id}/control")
    def change_workflow(workflow_id: str, body: WorkflowControl, request: Request):
        request.app.state.runtime.workflows.set_enabled(workflow_id, body.enabled)
        return {"enabled": body.enabled}

    @app.post("/api/workflows/{workflow_id}/agent")
    def bind_workflow_agent(workflow_id: str, body: WorkflowAgentSelection, request: Request):
        request.app.state.runtime.workflows.set_agent(workflow_id, body.agent_id)
        return {"agent_id": body.agent_id}

    @app.post("/api/workflows/{workflow_id}/scan")
    def scan_workflow(workflow_id: str, request: Request):
        workflows = request.app.state.runtime.workflows
        try:
            return workflows.scan(workflow_id)
        except (Conflict, Missing):
            raise
        except Exception as error:
            raise HTTPException(status_code=503, detail=scan_failure_reason(error)) from error

    @app.get("/api/sessions")
    def list_sessions(request: Request):
        return request.app.state.runtime.tasks.list_sessions()

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: UUID, request: Request):
        return request.app.state.runtime.tasks.session_detail(str(session_id))

    @app.post("/api/sessions/{session_id}/name")
    def rename_session(session_id: UUID, body: SessionName, request: Request):
        request.app.state.runtime.tasks.rename_session(str(session_id), body.name)
        return {"name": body.name}

    @app.post("/api/sessions/{session_id}/resume", status_code=202)
    def resume_session(session_id: UUID, body: SessionResume, request: Request):
        return {"id": request.app.state.runtime.tasks.resume_session(str(session_id), body.instruction)}

    @app.post("/api/mock/git/{scenario}", status_code=202)
    def apply_mock_scenario(scenario: str, request: Request):
        git = request.app.state.runtime.mock_git
        if git is None or scenario not in git.scenarios:
            raise Missing("未找到 Mock Git 样例。")
        git.advance(scenario)
        return {"accepted": True}

    @app.get("/api/mock/git/comments")
    def mock_comments(request: Request):
        git = request.app.state.runtime.mock_git
        if git is None:
            raise Missing("当前模式未加载 Mock Git。")
        return git.comments()

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: UUID, request: Request):
        return request.app.state.runtime.tasks.detail(str(task_id))

    @app.post("/api/tasks/{task_id}/close")
    def close_task(task_id: UUID, body: TaskClose, request: Request):
        request.app.state.runtime.tasks.close(str(task_id), body.reason)
        return {"id": str(task_id), "status": "closed"}

    @app.post("/api/requests/{request_id}/reply", status_code=202)
    def reply(request_id: UUID, body: ReplyInput, request: Request):
        return {"id": request.app.state.runtime.tasks.reply(str(request_id), body.answer)}

    @app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
    def run_log(run_id: UUID, request: Request):
        return request.app.state.runtime.tasks.read_log(str(run_id))

    @app.post("/api/runs/{run_id}/recovery", status_code=202)
    def recover(run_id: UUID, body: RecoveryInput, request: Request):
        request.app.state.runtime.runner.recover(str(run_id), body.operation)
        return {"accepted": True}

    @app.get("/api/actions")
    def list_actions(request: Request):
        return request.app.state.runtime.outbox.list()

    @app.post("/api/actions/{action_id}/reconciliation", status_code=202)
    def reconcile(action_id: UUID, body: ReconciliationInput, request: Request):
        request.app.state.runtime.outbox.manual(str(action_id), body.decision, body.evidence, body.remote_id)
        return {"accepted": True}

    dist = PROJECT / "frontend" / "dist"
    app.mount("/assets", StaticFiles(directory=dist / "assets", check_dir=False), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        if not (dist / "index.html").is_file():
            return PlainTextResponse("管理台尚未构建。请在 frontend 目录运行 npm ci 和 npm run build。", status_code=503)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app(sdd_runner=os.environ.get("ALWAYS_ON_SDD_RUNNER", "mock"))


def create_claude_smoke_app():
    return create_app(claude_smoke=True)
