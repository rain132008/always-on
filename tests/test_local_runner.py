from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend import service
from backend.agent import MockAgent
from backend.api import create_app


class ClaudeStub(MockAgent):
    def __init__(self, _config_dir, *, smoke):
        self.mode = "claude-smoke" if smoke else "claude"


def wait_task(client, task_id, status):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        detail = client.get(f"/api/tasks/{task_id}").json()
        if detail["status"] == status:
            return detail
        sleep(0.06)
    raise AssertionError(f"任务未进入 {status} 状态，当前为 {detail['status']}。")


def test_live_sdd_pins_runner_across_service_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ClaudeAgent", ClaudeStub)
    with TestClient(create_app(tmp_path)) as client:
        previous_id = client.post("/api/tasks", json={"title": "切换前", "prompt": "请等待确认"}).json()["id"]
        previous = wait_task(client, previous_id, "waiting_human")

    with TestClient(create_app(tmp_path, sdd_runner="claude")) as client:
        providers = {item["id"]: item["runner"] for item in client.get("/api/workflows").json()}
        assert providers == {"sdd-task": "claude", "mr-review": "mock", "doc-maintenance": "mock"}
        request_id = previous["requests"][0]["id"]
        assert client.post(f"/api/requests/{request_id}/reply", json={"answer": "确认旧任务"}).status_code == 202
        old = wait_task(client, previous_id, "completed")
        assert [run["runner"] for run in old["runs"]] == ["mock", "mock"]
        assert old["runs"][1]["resume_id"] == previous["session"]["external_id"]

        live_id = client.post("/api/tasks", json={"title": "切换后", "prompt": "请等待确认"}).json()["id"]
        live = wait_task(client, live_id, "waiting_human")
        assert live["runs"][0]["runner"] == "claude"

    with TestClient(create_app(tmp_path)) as client:
        request_id = live["requests"][0]["id"]
        assert client.post(f"/api/requests/{request_id}/reply", json={"answer": "确认新任务"}).status_code == 202
        blocked = wait_task(client, live_id, "recovery_required")
        run = blocked["runs"][-1]
        assert run["runner"] == "claude" and run["pid"] is None
        retry = f"/api/runs/{run['id']}/recovery"
        assert client.post(retry, json={"operation": "retry"}).status_code == 409

    with TestClient(create_app(tmp_path, sdd_runner="claude")) as client:
        assert client.post(retry, json={"operation": "retry"}).status_code == 202
        done = wait_task(client, live_id, "completed")
        assert done["runs"][-1]["runner"] == "claude"
        assert done["runs"][-1]["resume_id"] == live["session"]["external_id"]
