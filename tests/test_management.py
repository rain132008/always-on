from time import monotonic, sleep, time
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.contracts import RunResult
from backend.database import Database, execute, rows
from backend.tasks import Tasks
from backend.workspace import Conflict


def test_session_center_resume_reuses_identity_and_has_state_gates(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        task_id = client.post("/api/tasks", json={"title": "原任务", "prompt": "回归"}).json()["id"]

        def completed(count):
            deadline = monotonic() + 8
            while monotonic() < deadline:
                task = client.get(f"/api/tasks/{task_id}").json()
                if len(task["runs"]) == count and task["status"] == "completed":
                    return task
                sleep(0.08)
            pytest.fail(f"任务尚未完成 {count} 轮：{task['status']}")

        deadline = monotonic() + 8
        while monotonic() < deadline:
            task = client.get(f"/api/tasks/{task_id}").json()
            if task["status"] == "waiting_human":
                break
            sleep(0.08)
        assert task["status"] == "waiting_human"
        session = task["session"]
        path = f"/api/sessions/{session['id']}"
        assert client.post(path + "/resume", json={"instruction": "绕过待办"}).status_code == 409
        assert client.post(f"/api/requests/{task['requests'][0]['id']}/reply", json={"answer": "确认"}).status_code == 202
        completed(2)
        assert client.post(path + "/name", json={"name": "已命名会话"}).status_code == 200
        assert client.post("/api/workflows/sdd-task/control", json={"enabled": False}).status_code == 200
        assert client.post(path + "/resume", json={"instruction": "已停用时尝试"}).status_code == 409
        assert client.post("/api/workflows/sdd-task/control", json={"enabled": True}).status_code == 200
        workspace = tmp_path / "mock-sdd"
        moved = workspace.rename(tmp_path / "moved-sdd")
        try:
            assert client.post(path + "/resume", json={"instruction": "工作区移除后尝试"}).status_code == 409
        finally:
            moved.rename(workspace)
        assert client.post(path + "/resume", json={"instruction": "补充本轮范围"}).status_code == 202
        result = completed(3)
        assert result["runs"][-1]["resume_id"] == session["external_id"]
        assert "补充本轮范围" in result["runs"][-1]["summary"]
        assert client.get(path).json()["name"] == "已命名会话"
        assert client.post(f"/api/tasks/{task_id}/close", json={}).status_code == 200
        assert client.post(path + "/resume", json={"instruction": "已关闭时尝试"}).status_code == 409


def test_close_fences_active_results_and_pending_subject_updates(tmp_path):
    database = Database(tmp_path / "core.sqlite3")
    database.migrate()
    actions = Mock()
    tasks = Tasks(database, tmp_path, tmp_path, outbox=None, result_actions=actions)

    def enqueue(event):
        with database.write() as conn:
            return tasks.submit(conn, "评审", event, "mr_head", event, time(),
                                workflow={"id": "review"}, subject="sample-mr")

    task_id = enqueue("head-1")
    run = tasks.claim(30)
    tasks.attach_process(run, 21, time())
    assert enqueue("head-2") == task_id
    tasks.close(task_id, "工作已结束")
    assert tasks.finish(run, RunResult(
        contract_version="1", task_id=task_id, run_id=run["run_id"], generation=run["generation"],
        session_id=uuid4(), outcome="completed", summary="迟到报告",
    ))
    assert enqueue("head-3") == task_id
    actions.apply.assert_not_called()
    detail = tasks.detail(task_id)
    assert detail["status"] == "closed" and detail["session"]["external_id"] is None
    assert detail["runs"][0]["status"] == "cancelled"
    assert tasks.claim(30) is None
    with database.engine.connect() as conn:
        assert [row["status"] for row in rows(conn, "SELECT status FROM wakeups")] == ["cancelled", "cancelled"]
    database.engine.dispose()


def test_workflow_switch_parks_queued_run_across_restart(tmp_path):
    database = Database(tmp_path / "core.sqlite3")
    database.migrate()
    tasks = Tasks(database, tmp_path, tmp_path, outbox=None)
    task_id = tasks.create("已排队", "等待工作流启用")
    with database.write() as connection:
        execute(connection, "INSERT INTO workflow_controls VALUES ('sdd-task', 0, :now)", now=time())
    database.engine.dispose()
    database = Database(tmp_path / "core.sqlite3")
    tasks = Tasks(database, tmp_path, tmp_path, outbox=None)
    with pytest.raises(Conflict, match="已停用"):
        tasks.create("新触发", "暂时暂停")
    assert tasks.claim(30) is None
    with database.write() as connection:
        execute(connection, "UPDATE workflow_controls SET enabled = 1")
    assert tasks.claim(30)["task_id"] == task_id
    database.engine.dispose()
