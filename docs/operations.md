# 首个里程碑运行手册

服务用于单机、单用户本机运行，监听回环地址。内网部署所需的身份认证和访问控制见 [内网交接](intranet-handoff.md)。

## Windows 启动

在项目根目录执行：

```powershell
uv sync --locked --python 3.13
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
uv run --locked uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

打开 [管理台](http://127.0.0.1:8000/)。按 Ctrl+C 关闭服务，原命令可重启。保持同一份运行目录以保留任务、会话、待办和日志。默认工作区位于 `runtime-data/mock-sdd`。可用 `ALWAYS_ON_DATA` 与 `ALWAYS_ON_SDD_ROOT` 指定路径，参见 [配置样例](../config.example)。

### 本机 SDD 使用真实 Claude Code

安装本页下方的固定版 CLI，由当前终端提供 `ANTHROPIC_API_KEY` 和 `ANTHROPIC_BASE_URL`，然后使用 `./start-local.ps1` 启动。脚本默认使用本机硅基流动通道已验的 `moonshotai/Kimi-K2.7-Code`，可以用 `ALWAYS_ON_CLAUDE_MODEL` 指定其他兼容通道的模型。MR 和文档维护继续走 Mock。页面顶部显示当前启动模式，工作流和任务详情可查看 Runner。

启动脚本复用现有数据库，真实 CLI 会话文件写入同一运行目录下的 `claude-config/`。仅新建 SDD 使用真实 CLI。已入队任务和既有会话继续使用原执行方式，恢复真实会话时需要同时保留 SQLite、CLI 会话和工作区。直接运行本页上方的 Uvicorn 命令默认进入纯 Mock；此时排到的真实任务进入待恢复。重新用脚本启动后，从任务页核验并重新排队。

真实 SDD 轮次会将任务文本、Agent 提示词及当前回复发送给配置的网关。CLI 关闭工具、MCP 和项目设置加载，目前适合文本分析、澄清和定时续跑。变更真实仓库与文档需要接入内网工作区和相应的执行权限。

## 人工验收

1. 新建 SDD 任务，等它进入「待人工回复」，记录页面的 Session ID。
2. 退出服务并重新启动，回到原任务，确认待办仍存在。
3. 填写确认意见并点击「回复并恢复会话」。确认任务完成、第二轮使用刚记录的 Session ID，且事件列表可查到本次回复。
4. 打开「外部动作」，查看人工待办的 Mock 通知及 Mock 远端回执。

如果运行进入「待恢复」，先检查运行记录。可从页面停止仍在运行且身份可确认的旧 Mock 进程。进程退出且原工作区恢复后，点击「核验并重新排队」。无法确认远端副作用时，先在远端核对，再从「外部动作」提交证据和核对结果。

## 管理会话、工作流和任务

- 在「会话中心」按会话名、任务名或 Session ID 查找，进入关联任务或检查 Run 历史。任务已完成或失败且已有外部 Session ID 时，可以提交补充指令继续原会话。等待人工时请在关联任务内回复待办。
- 在「工作流管理」查看启动时加载的 YAML、Task Run 和数据源扫描记录。停用状态跨重启保存，已排队任务留待再次启用。启用后从保存的游标补扫。需要改 YAML 时在配置目录编辑并重启服务。
- 在任务详情使用「关闭任务」。关闭取消待执行唤醒，已开始的轮次收尾，且关闭后的结果不会产生新待办或发布动作。需要清理历史故障留下的进程时，可从原任务停止旧进程。

## 配置执行 Agent

1. 打开「Agent 管理」，可用内置的通用任务、代码评审和文档维护 Agent，也可新增配置。填写名称、职责说明和任务提示词后保存。
2. 打开「工作流管理」，选择一个工作流，进入「Agent 配置」，选择执行 Agent 并保存绑定。可从 Agent 详情检查绑定的工作流，从工作流运行历史检查各轮使用的 Agent。
3. 保存后，新触发使用最新配置，已排队任务和原事件派生的继续操作沿用各自快照。绑定跨服务重启保留。工作流 YAML 仍负责输入输出协议、Skill 与结果动作。

真实 SDD 和独立 Claude 验收入口均使用已选 Agent 的任务提示词。Mock 工作流使用工作流协议模拟结果。

## 验证 Agent 延期续跑

本机 Mock 模式新建 SDD 任务，将说明填写为「预约 10 秒，请在同一会话复查」。任务进入「等待定时唤醒」后会显示到期时间，第二轮会使用原 Session。预约期间可重启服务验证到期时间持久化。工作流停用时保留预约，重新启用后处理到期项。关闭任务取消预约。

真实 Claude CLI 验收入口会在每轮传入 [平台系统提示词](../protocol/platform-prompt.md)。可用合成任务明确要求 Agent 先预约、到点后完成，或先提出人工问题、收到答复后继续。

## Mock MR 与文档维护验收

使用默认入口 `backend.api:app`。建议为每次完整演示设置新的 `ALWAYS_ON_DATA` 路径。同名样例重复投递会被去重。项目根目录启动后，在另一个 PowerShell 终端执行：

```powershell
$alwaysOnApi = 'http://127.0.0.1:8000/api'
Invoke-RestMethod "$alwaysOnApi/mock/git/mr_opened" -Method Post -ContentType 'application/json' -Body '{}'
Invoke-RestMethod "$alwaysOnApi/mock/git/comments"
```

待首条评论出现后，再用同一条 POST 命令将 `mr_opened` 换成 `mr_updated`。检查任务页中的两轮 Run 和两个 SHA，第二轮应使用相同 Session ID，Mock 远端应有两条评论。重复投递时两项计数保持一致。

```powershell
Invoke-RestMethod "$alwaysOnApi/mock/git/merged_batch" -Method Post -ContentType 'application/json' -Body '{}'
Invoke-RestMethod "$alwaysOnApi/workflows/doc-maintenance/scan" -Method Post -ContentType 'application/json' -Body '{}'
Invoke-RestMethod "$alwaysOnApi/workflows"
```

文档任务在管理台进入待人工回复。停止并重启服务，打开原任务回复，检查原 Session ID 和「工作流产出」中的文档建议。把 `merged_batch` 换成 `merged_increment` 后再扫描，只应产生后续的增量任务。默认计划在北京时间每天 02:00 扫描，手动扫描使用同一套游标和入库逻辑。

## 自动测试

```powershell
uv run --locked pytest -q
```

OpenAI 在线测试需显式启用。可用现有环境变量，或在 PowerShell 7 中按提示输入 Token。模型应为账号有权使用且支持 Structured Outputs 的模型。

```powershell
if (-not $env:OPENAI_API_KEY) { $env:OPENAI_API_KEY = Read-Host 'OpenAI API Token' -MaskInput }
$env:ALWAYS_ON_OPENAI_TEST_MODEL = '填入模型标识'
$env:ALWAYS_ON_OPENAI_LIVE = '1'
uv run --locked pytest -q -m openai_live
Remove-Item Env:ALWAYS_ON_OPENAI_LIVE
```

该测试发出一次 Responses API 请求，最多生成 512 个 token，使用合成数据和 `store: false`。请求结果通过同一个 Pydantic `RunResult` 校验。参见 [OpenAI Structured Outputs 文档](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。

## Claude Code 2.1.109 的真实验收

真实 Claude CLI 使用专用验收入口。在项目根目录安装固定版本并启动：

```powershell
npm.cmd install --registry https://registry.npmjs.org --prefix runtime-data/tools/claude-code-2.1.109 --no-save --no-audit --no-fund --ignore-scripts @anthropic-ai/claude-code@2.1.109
$env:ALWAYS_ON_CLAUDE_TEST_MODEL = 'moonshotai/Kimi-K2.7-Code'
uv run --locked uvicorn backend.api:create_claude_smoke_app --factory --host 127.0.0.1 --port 8000
```

这里的模型名对应本机已有的硅基流动通道。其他通道使用对应的模型名和 `ANTHROPIC_BASE_URL`，将该通道的 `ANTHROPIC_API_KEY` 通过环境变量传给服务。验收入口为 Claude CLI 校验精确版本，服务数据与会话文件保存在独立的 `runtime-data/claude-smoke/`。该入口只用于合成任务，关闭 Claude 工具、MCP 和项目设置加载。按本页人工验收第 1 至 3 步验证创建、停服和原会话接续。参见 [Claude Code CLI 参数](https://code.claude.com/docs/en/cli-reference) 和 [硅基流动的 Claude Code 接入说明](https://docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-ClaudeCode)。
