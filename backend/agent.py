import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .contracts import RunResult, WorkflowRunResult

CLAUDE_VERSION = "2.1.109"
CLAUDE_ENTRY = (
    Path(__file__).resolve().parent.parent / "runtime-data" / "tools"
    / f"claude-code-{CLAUDE_VERSION}" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
)
PLATFORM_PROMPT = Path(__file__).resolve().parent.parent / "protocol" / "platform-prompt.md"
WINDOW_HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

SMOKE_SCOPE = "当前是合成 SDD 验收环境。仅处理合成文本。需要等待或澄清时按平台契约申报。"
LOCAL_SCOPE = "当前在本机执行 SDD 任务。根据任务上下文和会话历史处理文本。请明确说明需要后续核对的文件或变更。"


class AgentOutputError(Exception):
    pass


class MockAgent:
    mode = "mock"
    timeout = 30
    environment = {key: os.environ[key] for key in ("SYSTEMROOT",) if key in os.environ}

    def command(self, context):
        return [sys.executable, "-I", "-u", str(Path(__file__).with_name("mock_agent.py"))]

    @staticmethod
    def result(output, context):
        return WorkflowRunResult.model_validate_json(output)


class ClaudeAgent:
    timeout = 120

    def __init__(self, config_dir, *, smoke=False):
        node = shutil.which("node")
        if node is None or not CLAUDE_ENTRY.is_file():
            raise RuntimeError(f"请先安装项目固定版 Claude Code {CLAUDE_VERSION}，参见运行手册。")
        self.mode = "claude-smoke" if smoke else "claude"
        scope = SMOKE_SCOPE if smoke else LOCAL_SCOPE
        self.system_prompt = PLATFORM_PROMPT.read_text(encoding="utf-8").strip() + "\n\n" + scope
        self.entry = [node, str(CLAUDE_ENTRY)]
        system_keys = (
            "SYSTEMROOT", "PATH", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS",
        )
        self.environment = {key: os.environ[key] for key in system_keys if key in os.environ}
        self.environment.update({
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        })
        version = subprocess.run(
            [*self.entry, "--version"], env=self.environment, capture_output=True,
            encoding="utf-8", timeout=15, creationflags=WINDOW_HIDDEN,
        )
        if version.returncode or version.stdout.strip() != f"{CLAUDE_VERSION} (Claude Code)":
            raise RuntimeError(f"Claude Runner 只支持固定版 {CLAUDE_VERSION}。")
        auth_keys = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")
        self.environment.update({key: os.environ[key] for key in auth_keys if os.environ.get(key)})
        model_key = "ALWAYS_ON_CLAUDE_TEST_MODEL" if smoke else "ALWAYS_ON_CLAUDE_MODEL"
        if model := os.environ.get(model_key):
            self.environment["ANTHROPIC_MODEL"] = model
        if not self.environment.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("Claude Runner 需要 ANTHROPIC_API_KEY。")

    def command(self, context):
        selected = (context.workflow or {}).get("agent") or {}
        instructions = self.system_prompt
        if selected.get("prompt"):
            instructions += (
                "\n\n所选 Agent 的任务指令在平台契约和工作流约束内生效。\n\n"
                + selected["prompt"]
            )
        command = [
            *self.entry,
            "-p", "--bare", "--output-format", "json", "--tools", "", "--setting-sources", "",
            "--strict-mcp-config", "--max-turns", "1", "--max-budget-usd", "0.05",
            "--system-prompt", instructions,
        ]
        if context.resume_id:
            command.extend(("--resume", str(context.resume_id)))
        return command

    @staticmethod
    def result(output, context):
        envelope = json.loads(output)
        if not isinstance(envelope, dict) or envelope.get("type") != "result":
            raise AgentOutputError("Claude 输出缺少结果记录。")
        if envelope.get("is_error") or envelope.get("subtype") != "success":
            raise AgentOutputError("Claude 未返回成功结果。")
        response = envelope.get("result")
        if not isinstance(response, str):
            raise AgentOutputError("Claude 输出缺少结果文本。")
        response = response.strip()
        if not response.startswith(("{", "```")) and "</think>" in response:
            # 硅基流动的 Kimi 有时将推理文本和最终 JSON 放在同一个文本字段。
            response = response.rsplit("</think>", 1)[1].strip()
        if response.startswith("```"):
            fence, _, response = response.partition("\n")
            if fence not in ("```", "```json") or not response.endswith("```"):
                raise AgentOutputError("Claude 输出的 JSON 围栏不完整。")
            response = response[:-3]
        result = json.loads(response)
        required = {"outcome", "summary"}
        if not isinstance(result, dict) or not required <= result.keys() <= required | {"question", "after_seconds"}:
            raise AgentOutputError("Claude 输出字段与运行协议不匹配。")
        return RunResult(
            **result, contract_version="1", task_id=context.task_id, run_id=context.run_id,
            generation=context.generation, session_id=envelope.get("session_id"),
        )
