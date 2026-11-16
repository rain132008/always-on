from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend.api import create_app


def wait_for(read, ready):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        result = read()
        if ready(result):
            return result
        sleep(0.06)
    raise AssertionError(f"等待状态超时：{result}")


def test_binding_survives_restart_and_pending_reply_keeps_previous_agent(tmp_path):
    settings = {"name": "交接验证 Agent", "description": "验证保存与续跑", "prompt": "以交接为重点。"}
    workflow = "/api/workflows/sdd-task"
    with TestClient(create_app(tmp_path)) as client:
        assert {item["id"] for item in client.get("/api/agents").json()} == {"sdd-default", "review", "document"}
        assert client.get(workflow).json()["selected_agent"]["id"] == "sdd-default"
        created = client.post("/api/agents", json=settings)
        assert created.status_code == 201
        custom = created.json()["id"]
        assert client.post(workflow + "/agent", json={"agent_id": custom}).status_code == 200
        task_id = client.post("/api/tasks", json={"title": "首次配置", "prompt": "请等待确认"}).json()["id"]
        old = wait_for(lambda: client.get(f"/api/tasks/{task_id}").json(), lambda task: task["status"] == "waiting_human")
        assert old["runs"][0]["agent_id"] == custom

        edited = settings | {"name": "改名后的 Agent", "prompt": "使用更新的交接提示词。"}
        assert client.post(f"/api/agents/{custom}", json=edited).status_code == 200
        assert client.post(workflow + "/agent", json={"agent_id": "document"}).status_code == 200
        assert client.post(workflow + "/agent", json={"agent_id": "missing-agent"}).status_code == 404

    with TestClient(create_app(tmp_path)) as client:
        assert client.get(workflow).json()["selected_agent"]["id"] == "document"
        saved = next(item for item in client.get("/api/agents").json() if item["id"] == custom)
        assert saved["name"] == edited["name"] and saved["prompt"] == edited["prompt"]
        response = client.post(f"/api/requests/{old['requests'][0]['id']}/reply", json={"answer": "原会话确认"})
        assert response.status_code == 202
        done = wait_for(lambda: client.get(f"/api/tasks/{task_id}").json(), lambda task: task["status"] == "completed")
        assert done["runs"][1]["resume_id"] == old["session"]["external_id"]
        assert [run["agent_name"] for run in done["runs"]] == [settings["name"]] * 2

        new_id = client.post("/api/tasks", json={"title": "新的触发", "prompt": "验证新绑定"}).json()["id"]
        new_task = wait_for(lambda: client.get(f"/api/tasks/{new_id}").json(), lambda task: task["status"] == "waiting_human")
        assert new_task["runs"][0]["agent_id"] == "document"


def test_mr_agent_binding_preserves_review_contract(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/workflows/mr-review/agent", json={"agent_id": "document"}).status_code == 200
        assert client.post("/api/mock/git/mr_opened", json={}).status_code == 202
        comments = wait_for(lambda: client.get("/api/mock/git/comments").json(), bool)
        assert comments[0]["comment"]["head_sha"]
        detail = client.get("/api/workflows/mr-review").json()
        assert detail["agent"]["profile"] == "review"
        assert detail["selected_agent"]["id"] == "document"
        assert detail["runs"][0]["agent_name"] == "文档维护 Agent"
