# Always-on 内网联调交接

当前版本可以进入**隔离测试环境**联调。已交付 Task、Session、Wakeup、Runner、Outbox 和管理台，以及 Mock MR 和文档维护闭环。本机曾用 Claude Code `2.1.109` 经外部测试网关验证 SDD 续跑。真实内网 Git、Skill、模型网关、工作区权限及通知均待接入，测试记录见 [MVP 验收报告](../mvp-validation-report.md)。

## 1. 在内网启动基线

使用 Windows PowerShell，在仓库根目录执行。准备 `uv`、Python `3.13`、Node.js 和 npm。离线环境由内网运维提前准备锁文件对应的依赖、镜像或缓存，以及 Claude Code `2.1.109` 的批准安装包。

```powershell
uv sync --locked --python 3.13
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
uv run --locked pytest -q

$env:ALWAYS_ON_DATA = Join-Path $PWD.Path 'runtime-data\intranet-mock-check'
$env:ALWAYS_ON_WORKFLOWS_DIR = Join-Path $PWD.Path 'workflows'
$env:ALWAYS_ON_SDD_RUNNER = 'mock'
Remove-Item Env:ALWAYS_ON_SDD_ROOT -ErrorAction SilentlyContinue
uv run --locked uvicorn backend.api:app --host 127.0.0.1 --port 8010 --no-access-log
```

另开终端检查 `/api/health` 返回 `mode=mock`、`worker_alive=true`，并从 `/api/workflows` 确认三个工作流均加载。基线应为 24 项自动测试通过，OpenAI 在线项默认跳过。Mock 样例调用将示例端口改为 `8010`。 [本机管理台](http://127.0.0.1:8010/) · [Mock 验收命令](operations.md#mock-mr-与文档维护验收)

基线通过后按 Ctrl+C 停服，再用单独数据目录配置真实 SDD。以下用当前终端变量从内网运维提供的环境或密钥管理取值。必须填写实际内网网关、模型和已创建的测试 SDD 根目录。

```powershell
npm.cmd install --prefix runtime-data/tools/claude-code-2.1.109 --no-save --no-audit --no-fund --ignore-scripts @anthropic-ai/claude-code@2.1.109
node runtime-data/tools/claude-code-2.1.109/node_modules/@anthropic-ai/claude-code/cli.js --version

$env:ALWAYS_ON_DATA = Join-Path $PWD.Path 'runtime-data\intranet-pilot'
$env:ALWAYS_ON_SDD_ROOT = 'D:\内网联调\sdd-test'
$env:ANTHROPIC_BASE_URL = 'https://填写内网网关'
$env:ALWAYS_ON_CLAUDE_MODEL = '填写内网模型名'
$env:ALWAYS_ON_SDD_RUNNER = 'claude'
if (-not $env:ANTHROPIC_API_KEY) { throw '请先由内网密钥管理注入 ANTHROPIC_API_KEY' }
uv run --locked uvicorn backend.api:app --host 127.0.0.1 --port 8010 --no-access-log
```

安装命令使用已配置的内网 npm 镜像。使用离线工件时，将完整的固定版 CLI 安装产物放到同一路径。CLI 版本必须显示 `2.1.109 (Claude Code)`，Runner 会检查该路径及版本。`/api/health` 应为 `mixed`，`/api/workflows` 应为 `sdd-task=claude`、`mr-review=mock`、`doc-maintenance=mock`。健康接口只表明服务启动和 Dispatcher 在运行，首条 SDD 任务用于验证网关授权及调用。直接启动 Uvicorn 会避开 `start-local.ps1` 的外部测试模型默认值。

真实 Git 接入前，为内网联调复制 `workflows/` 到单独配置目录，并让 `ALWAYS_ON_WORKFLOWS_DIR` 指向它。目录应保留手动工作流 `sdd-task`。启动时加载 YAML，修改后重启。切换 Connector 时使用独立数据目录，或为新增试点工作流使用独立 ID，并保留旧 Connector 直到其已入队任务和动作处理完毕。续跑和重启均使用原运行账号、原工作区及同一份运行数据，真实 CLI 会话位于 `ALWAYS_ON_DATA/claude-config/`。初期只监听回环地址，跨用户开放前补齐鉴权及运行权限。

## 2. 首轮联调顺序

| 顺序 | 操作 | 通过标准 |
| --- | --- | --- |
| 1. Mock 基线 | 页面创建 SDD 并回复，投递样例 MR 和合并提交。 | SDD 同会话完成，MR 产生 Mock 评论，文档澄清后生成结构化建议。排除迁移、构建或调度问题后再接内网组件。 |
| 2. 内网 Claude | 用合成文本新建 SDD，让首轮请求一个人工确认，记下 Session ID。服务重启后在页面回复。再用另一条任务测试预约 20 秒并重启。 | 两轮使用原 CLI Session，Run 可查。预约到期只续跑一次。工作流停用期间不触发，重新启用后补跑。任务关闭会取消预约。 |
| 3. 工作区和异常恢复 | 用一次性测试目录验证工作区错配，执行中中断服务并检查旧进程。 | 原目录缺失或身份变化时拒绝 Resume。旧进程仍在运行或无法确认退出时不得并发补跑。核对残留进程与原目录后才可重排。 |
| 4. Git 只读与 Skill | 在专用测试项目接入 MR/合并增量读取，先移除 `publish_review`。用脱敏 Diff 单独调用实际 Skill，再接到 Runner。 | 同 MR 快速提交两个 SHA，重复投递不增加相同 SHA 的任务，后续评审沿用原 Session。Diff 和结果回显指定 SHA。多仓一仓失败时整批游标不推进，恢复后无丢失或重复。 |
| 5. 测试评论与文档 | 仅对指定测试 MR 开启 `publish_review`，模拟远端已写、客户端超时或本地回执未落库。文档运行先保留 `save_document_suggestion`。 | 远端评论仅一条，原 ID 能核对回写，无法确认时可在管理台人工处理。旧 head 或已关闭 MR 不发布。文档跨重启完成澄清，只保存结构化建议。 |

每个场景记录环境、工作流、实际 Skill 版本、Task/Run/Session ID、head 或游标、结果与证据。评论场景另记业务键和远端评论 ID。测试输入和日志使用脱敏数据。

## 3. 接真实 Git

- **装配和配置** 在 `backend/connectors/` 实现 `backend/connectors/git.py` 的 `GitConnector`，在 `backend/service.py` 用 `register_git(registry, name, adapter)` 注册与 `mock-git` 不同的名称。配置 `source.connector` 指向该名称，MR 和文档分别保持 `merge_requests`、`merged_commits` 操作。MR 的 `source.options` 当前为空，测试项目允许列表放在适配器配置。文档项目和目标分支放在 `source.options.repositories`，每项包含 `project_id`、`branch`。API 地址、账号和凭据由部署注入。
- **读取和游标** `discover_mr_events(after)` 返回事件和新游标。记录冷启动基线及并列时间戳的顺序规则，在适配器内取完远端分页。`get_mr` 返回权威状态和当前 head，`get_diff(ref, head_sha)` 获取指定版本并回显 SHA。`merged_commits(repository, after)` 按仓库与分支返回 SHA、摘要、Diff 和游标。流断档、不可读、超限或 SHA 不匹配时显式失败并保留原游标。现有代码对任一仓读取失败保留整批原游标。
- **评论和核对** `publish_comment` 接收 Outbox 生成的稳定业务键，根据内网平台能力将它写入可精确检索的评论标记或远端幂等键。先查同键记录，查不到再核对 MR 仍 `open` 且 head 匹配，成功返回远端评论 ID。旧状态用 `DeliveryObsolete`，只有确认未接收才用 `DeliveryRejected`。远端支持按版本条件写入时优先使用。缺少该能力时须在测试仓复验「核对后、发送前新 head」的竞态，再决定是否开放自动发布。
- **未知结果** `reconcile_comment` 返回 `found` 并附原评论 ID、确认未落地时返回 `absent`，其余返回 `unknown`。超时、索引延迟或查询不全按 `unknown` 处理。Outbox 进入 `needs_reconcile` 后在「外部动作」登记核对证据，避免直接重发。停用工作流会暂停新触发，已入队 Outbox 仍独立处理，因此首次启用测试评论前要确认测试账号权限仅覆盖目标项目。

签名及更多输入输出约束见 [Connector 与工作流契约](connector-contracts.md)。

## 4. 接真实 Skill

- 从内网负责人取得 Review Skill、文档 Skill、版本标识与样例。YAML 的 `agent.profile` 保持对应的数据协议 `review` 或 `document`，`agent.skill.id`、`agent.skill.version` 填真实值。页面的 Agent 绑定只决定名称及任务提示词，Workflow YAML 中的 Skill 版本由新事件快照固化。
- 当前 `backend/service.py` 仅给新 SDD 配真实 CLI。真实 MR 和文档需要扩展 Runner 路由，使这两个试点工作流在**入队时**保存内网 Runner，并在执行端按快照中的 Skill ID 和版本加载受控内容。当前 `backend/agent.py` 的文本验收解析只接受 `outcome`、`summary`、`question`、`after_seconds`，需为业务适配生成 `WorkflowRunResult.output`。调用 CLI 时保留平台提示词、会话参数和已选的 Agent 提示词。无法加载指定版本时让 Run 明确失败，既有会话继续使用原快照。
- MR 将 `ReviewInput` 的 MR 与固定 SHA Diff 交给 Skill。完成结果提供 `ReviewOutput.reviewed_sha` 和 `findings[]`，每项包含 `path`、`detail`。文档传入 `MergedCommit`，澄清回复在下一轮 `input_summary` 中，完成填写 `DocumentSuggestion.commit_sha`、`document`、`suggestion`。Runner 使用自身记录的 Task、Run、代次和 CLI 返回的 Session ID 封装结果，核心层核对 SHA 并执行结果动作。公司评审规则由对应 Skill 提供。
- 验收实际加载的版本、合规样例及错 SHA、无输出、缺 Skill 的失败路径。变更 Agent 或升级 Skill 后，新触发采用新配置。原待办、预约和重试仍用原事件配置。阶段一的文档动作只保存建议，真实写入与审批由内网负责人定义。

运行上下文见 [Agent 协议](../protocol/agent-contract.md) 和 [平台提示词](../protocol/platform-prompt.md)。

## 5. 接真实 SDD 工作区

- **目录和账户** 由内网管家盘点 SDD 根目录、各业务仓路径、运行账号、只读工具及可写目标。用 `ALWAYS_ON_SDD_ROOT` 绑定已经存在的一次性测试根目录，Claude 仍以该根为当前目录启动。`backend/workspace.py` 核验规范化路径和文件系统身份；数据库、CLI 会话存储、运行账号和目录身份都需要持续可用。
- **先文本，后只读** 基线 Runner 禁用文件工具、MCP 和项目设置加载。接入真实只读工具时，在执行环境或操作系统层限制写权限、允许目录和工具范围。在允许目录内验证实际读取，并在测试目录外及禁止路径验证访问被拒绝。共享根目录先用于只读任务。
- **写入另行验收** 首版仍用现有单 Dispatcher，实际写入需落实单任务串行、受控分支和明确的可写路径。用测试仓证明任务读写范围和外部子进程受控，无法越界写入，再放开写任务。后续需多仓隔离时，从 `backend/tasks.py` 的 Session 绑定入口选取任务独立工作区，并沿用当前身份校验。续跑沿用原路径和目录身份。
- **恢复与运维** 试验路径删除、同路径更换目录、CLI 会话文件丢失和运行账号权限不足。在测试任务运行期间中断，验证工具启用后的真实进程树能被停止且可确认退出。回滚应用前停服并备份整套 SQLite、CLI 会话和运行日志。恢复时保留工作区。若有远端结果未知，先核对外部状态再决定是否重排或重发。

## 6. 试点放行与后续项

- 先放行指定内网网关下的 SDD 文本及只读试点。Git 评论、真实文档写入按各自验收和权限逐项放开。暂缓时可停用相关工作流，已有外部动作按 Outbox 核对流程处理。
- 访问控制需覆盖管理台、API、日志、任务数据与 Agent/工作流配置。通知待实现 Outbox Connector、内网收件人映射、失败重试和远端核对，并用测试收件人验收。试点期间可用站内人工待办和 Mock 通知。
- 已有管理能力复验证据见 [P3.5 管理功能复验](../mvp-validation-report.md#p35-管理功能复验)。内网仍要验证会话和工作区权限、工作流停用后的补扫、关闭任务后的 Wakeup 取消及迟到结果拦截。
