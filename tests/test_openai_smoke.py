import json
import os

import httpx
import pytest

from backend.contracts import RunResult


@pytest.mark.openai_live
def test_openai_returns_run_result():
    if os.environ.get("ALWAYS_ON_OPENAI_LIVE") != "1":
        pytest.skip("未启用 OpenAI 在线测试")
    token = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("ALWAYS_ON_OPENAI_TEST_MODEL", "").strip()
    if not token or not model:
        pytest.fail("在线测试需要 OPENAI_API_KEY 和 ALWAYS_ON_OPENAI_TEST_MODEL。")

    expected = {
        "task_id": "01111111-1111-4111-8111-111111111111",
        "run_id": "02222222-2222-4222-8222-222222222222",
        "session_id": "03333333-3333-4333-8333-333333333333",
        "contract_version": "1", "generation": 1, "outcome": "completed",
    }
    schema = RunResult.model_json_schema()
    for name, field_type in schema["properties"].items():
        if name == "question":
            schema["properties"][name] = {"type": ["string", "null"]}
            continue
        if name == "after_seconds":
            schema["properties"][name] = {"type": ["integer", "null"]}
            continue
        for keyword in ("format", "minLength", "maxLength", "minimum"):
            field_type.pop(keyword, None)
        if "const" in field_type:
            field_type["enum"] = [field_type.pop("const")]
    schema["required"] = list(schema["properties"])
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": model, "store": False, "max_output_tokens": 512,
            "input": "请返回模拟任务的 RunResult。固定字段如下，summary 写“合成测试完成”，question 和 after_seconds 为 null。"
            + json.dumps(expected),
            "text": {"format": {"type": "json_schema", "name": "run_result", "strict": True, "schema": schema}},
        },
        timeout=30,
    )
    if not response.is_success:
        pytest.fail(f"OpenAI 返回 HTTP {response.status_code}，请求 ID {response.headers.get('x-request-id', '无')}。")
    payload = response.json()
    assert payload["status"] == "completed", "OpenAI 未完成测试回复"
    content = [
        part["text"] for item in payload["output"] if item["type"] == "message"
        for part in item["content"] if part["type"] == "output_text"
    ]
    assert len(content) == 1, "缺少结构化测试回复"
    actual = RunResult.model_validate_json(content[0]).model_dump(mode="json")
    assert {field: actual[field] for field in expected} == expected
