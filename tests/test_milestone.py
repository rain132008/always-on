import json
import subprocess
import sys
from time import monotonic, sleep
from unittest.mock import Mock
from uuid import uuid4

import psutil
import pytest
from fastapi.testclient import TestClient

from backend.agent import AgentOutputError, ClaudeAgent
from backend.api import create_app
from backend.connectors.mock import MockRemote
from backend.contracts import RunResult, TaskContext
from backend.database import Database, execute
from backend.outbox import DeliveryRejected, Outbox, mr_review_key
from backend.tasks import Tasks


def poll(client, task_id, status):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        task = response.json()
        if task["status"] == status:
            return task
        if task["status"] in ("failed", "recovery_required"):
            pytest.fail(f"任务意外停止 {task['runs']}")
        sleep(0.05)
    pytest.fail(f"任务未到达 {status}，最近状态 {task}")


def new_task(client):
    response = client.post("/api/tasks", json={"title": "SDD 回归", "prompt": "模拟处理需求"})
    assert response.status_code == 201
    return response.json()["id"]


def send_reply(client, request_id):
    return client.post(f"/api/requests/{request_id}/reply", json={"answer": "仅验证首次接续"})


def test_claude_uses_result_after_reasoning_and_owns_run_identity():
    context = TaskContext(
        task_id=uuid4(), run_id=uuid4(), generation=2, resume_id=uuid4(),
        wake_reason="human_replied", title="验收任务", prompt="测试启动", input_summary="已确认范围",
    )
    payload = {"outcome": "completed", "summary": "已确认范围", "question": None}
    envelope = {
        "type": "result", "subtype": "success", "is_error": False, "session_id": str(context.resume_id),
        "result": "推理文本</think>" + json.dumps(payload, ensure_ascii=False),
    }
    result = ClaudeAgent.result(json.dumps(envelope).encode(), context)
    assert (result.session_id, result.run_id, result.generation) == (context.resume_id, context.run_id, 2)
    envelope["result"] = json.dumps(payload | {"run_id": str(uuid4())})
    with pytest.raises(AgentOutputError):
        ClaudeAgent.result(json.dumps(envelope).encode(), context)


def test_human_reply_after_restart_keeps_session(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        task_id = new_task(client)
        waiting = poll(client, task_id, "waiting_human")
        session_id = waiting["session"]["external_id"]
        assert session_id
        assert len(waiting["runs"]) == 1 and waiting["runs"][0]["pid"]
    with TestClient(create_app(tmp_path)) as client:
        request_id = waiting["requests"][0]["id"]
        assert send_reply(client, request_id).status_code == 202
        assert send_reply(client, request_id).status_code == 202
        completed = poll(client, task_id, "completed")
        assert len(completed["events"]) == len(completed["runs"]) == 2
        assert completed["session"]["external_id"] == completed["runs"][1]["resume_id"] == session_id
        log = client.get(f"/api/runs/{completed['runs'][1]['id']}/log")
        assert "human_replied" in log.text


def test_orphan_run_blocks_recovery_and_late_result(tmp_path, monkeypatch):
    root = tmp_path / "mock-sdd"
    root.mkdir()
    database = Database(tmp_path / "always-on.sqlite3")
    database.migrate()
    store = Tasks(database, root, tmp_path / "logs", None)
    task_id = store.create("旧进程", "重启后先核对原进程")
    run = store.claim(timeout=30)
    old = subprocess.Popen(
        [sys.executable, "-I", "-c", "from time import sleep; sleep(90)"], cwd=root,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        store.attach_process(run, old.pid, psutil.Process(old.pid).create_time())
        with TestClient(create_app(tmp_path)) as client:
            blocked = poll(client, task_id, "recovery_required")
            assert len(blocked["runs"]) == 1 and old.poll() is None
            route = f"/api/runs/{run['run_id']}/recovery"
            assert client.post(route, json={"operation": "retry"}).status_code == 409
            with monkeypatch.context() as patch:
                patch.setattr("backend.runner.probe", lambda _run: ("unknown", None))
                assert client.post(route, json={"operation": "retry"}).status_code == 409
                assert client.post(route, json={"operation": "stop"}).status_code == 409
                assert old.poll() is None
            stale = RunResult(
                contract_version="1", task_id=task_id, run_id=run["run_id"], generation=run["generation"],
                session_id=run["session_id"], outcome="completed", summary="过期结果",
            )
            assert store.finish(run, stale) is False
            assert client.post(route, json={"operation": "stop"}).status_code == 202
            old.wait(timeout=3)
            assert client.post(route, json={"operation": "retry"}).status_code == 202
            waiting = poll(client, task_id, "waiting_human")
            assert [(item["generation"], item["status"]) for item in waiting["runs"]] == [
                (1, "interrupted"), (2, "needs_human"),
            ]
            assert store.finish(run, stale) is False
    finally:
        if old.poll() is None:
            old.kill()
            old.wait(timeout=3)
        database.engine.dispose()


@pytest.mark.parametrize("replace", [False, True], ids=["missing", "replaced"])
def test_changed_workspace_blocks_resume(tmp_path, replace):
    root = tmp_path / "sdd"
    root.mkdir()
    backup = tmp_path / "sdd-original"
    data = tmp_path / "data"
    with TestClient(create_app(data, root)) as client:
        task_id = new_task(client)
        waiting = poll(client, task_id, "waiting_human")
    root.rename(backup)
    if replace:
        root.mkdir()
    with TestClient(create_app(data, root)) as client:
        assert send_reply(client, waiting["requests"][0]["id"]).status_code == 202
        blocked = poll(client, task_id, "recovery_required")
        run = blocked["runs"][-1]
        assert run["pid"] is None
        route = f"/api/runs/{run['id']}/recovery"
        assert client.post(route, json={"operation": "retry"}).status_code == 409
        if replace:
            root.rmdir()
        backup.rename(root)
        assert client.post(route, json={"operation": "retry"}).status_code == 202
        completed = poll(client, task_id, "completed")
        assert completed["runs"][-1]["resume_id"] == waiting["session"]["external_id"]


@pytest.mark.parametrize("case", ["success", "rejected", "timeout", "lost_local_record", "no_reconcile"])
def test_outbox_verifies_unknown_delivery_before_retry(tmp_path, case):
    database = Database(tmp_path / "always-on.sqlite3")
    database.migrate()
    remote = MockRemote(tmp_path / "remote.sqlite3")
    connector = Mock(spec=["deliver"] if case == "no_reconcile" else ["deliver", "reconcile"])
    key = mr_review_key("mock", "test-project", 1, "head-c1", "review-skill-v1")
    payload = {"comment": "模拟评审"}
    if case != "no_reconcile":
        connector.reconcile.side_effect = remote.reconcile

    def deliver(*args):
        if case == "rejected":
            raise DeliveryRejected()
        if case == "no_reconcile":
            raise TimeoutError()
        result = remote.deliver(*args)
        if case == "timeout":
            raise TimeoutError()
        return result

    connector.deliver.side_effect = deliver
    outbox = Outbox(database, {"mock-git": connector})
    with database.write() as connection:
        action = outbox.enqueue(connection, "mock-git", "mr_review", key, payload)
        assert outbox.enqueue(connection, "mock-git", "mr_review", key, payload) == action

    if case == "lost_local_record":
        claimed = outbox.claim()
        assert claimed["id"] == action
        connector.deliver("mr_review", key, payload)
        assert outbox.list()[0]["status"] == "sending"
        database.engine.dispose()
        database = Database(tmp_path / "always-on.sqlite3")
        outbox = Outbox(database, {"mock-git": connector})
        outbox.recover_interrupted()
        assert outbox.list()[0]["status"] == "reconciling"
    else:
        outbox.tick()

    if case in {"timeout", "lost_local_record", "no_reconcile"}:
        assert outbox.list()[0]["status"] == "reconciling"
        outbox.tick()
    if case == "no_reconcile":
        assert outbox.list()[0]["status"] == "needs_reconcile"
        with TestClient(create_app(tmp_path)) as client:
            checked = client.post(f"/api/actions/{action}/reconciliation", json={
                "decision": "delivered", "evidence": "已在测试远端确认回执", "remote_id": "verified-id",
            })
            assert checked.status_code == 202
            assert client.get("/api/actions").json()[0]["remote_id"] == "verified-id"
    elif case == "rejected":
        assert outbox.list()[0]["status"] == "pending"
        with database.write() as connection:
            execute(connection, "UPDATE external_actions SET due_at = 0 WHERE id = :id", id=action)
        connector.deliver.side_effect = remote.deliver
        outbox.tick()
    if case != "no_reconcile":
        assert outbox.list()[0]["status"] == "sent"
        assert outbox.list()[0]["remote_id"]
    if case in {"timeout", "lost_local_record"}:
        connector.reconcile.assert_called_once_with("mr_review", key, payload)
    assert connector.deliver.call_count == (2 if case == "rejected" else 1)
    database.engine.dispose()
