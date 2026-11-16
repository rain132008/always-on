# Connector 与工作流契约

## 注册

`backend/service.py` 是装配入口。`ConnectorRegistry.register_source` 注册 Connector 名、操作名、选项模型和读取函数，读取函数接收已校验选项与已存游标，返回 `SourceBatch(items, cursors)`。每个待处理项提供稳定事件键、主题键、事件类型、标题、摘要和 JSON 输入。注册完成后，选用已有操作、Profile 和结果动作的新工作流只需增加 YAML。

`backend/git_workflows.py` 的 `register_git` 将 Git 端口绑定成 `merge_requests`、`merged_commits` 两个来源和评论动作 Connector。真实适配器按 [内网交接](intranet-handoff.md) 在装配入口注册，使用单独的 Connector 名。Mock 可以与真实适配器并存，用于脱网回归。

## Git 端口

类型及约束见 `backend/connectors/git.py`。以下游标为 Connector 提供的不透明字符串，核心层按工作流及数据流保存。

| 方法 | 请求与返回 | 调用约定 |
| --- | --- | --- |
| `discover_mr_events(after)` | `Page[MREvent]`，含 MR 标识和事件种类 | 返回上一游标之后、截至新游标的完整批次。首轮 `after=None` 的基线由适配器建立。网关分页时先在适配器内取全。 |
| `get_mr(ref)` | `MRRef(provider, project_id, iid)` → 标题、状态、当前 head SHA | 重查权威 MR，事件载荷仅提供 MR 身份。 |
| `get_diff(ref, head_sha)` | `MRDiff(head_sha, text)` | 必须取指定 SHA 并回显。Diff 保留原文本，目前单条上限 120,000 字符。 |
| `merged_commits(repository, after)` | `Repository(project_id, branch)` → `Page[MergedCommit]`，含仓库、分支、SHA、摘要、Diff | 每个仓分支独立推进。空批次可保留游标。任一仓读取失败时，整次扫描保留原游标。 |
| `publish_comment(key, comment)` | 稳定去重键、MR 标识、head、摘要、发现 → 远端评论 ID | 发布前核对 MR 仍开放且 head 一致。若已发同一业务键，应返回已存在的远端 ID。确认旧结果过期时抛出 `DeliveryObsolete`，确认远端未接收的可重试终止抛出 `DeliveryRejected`。调用结果未知时由 Outbox 核对。 |
| `reconcile_comment(key, comment)` | `found + remote_id`、`absent` 或 `unknown` | 远端评论可能延迟出现时返回 `unknown`，只有确认未落地才返回 `absent`。 |

`fixtures/mock-git.json` 提供 MR 初次提交和新 head，以及两个仓库的合并批次和后续单仓增量。Mock 远端持久化事件和评论，可验证出站动作在服务重启后按业务键核对。

## Agent 数据

- MR 输入为 `ReviewInput`，包含 MR 标识、当前 head 和指定 SHA 的 Diff。成功结果的 `output` 为 `ReviewOutput`，要求 `reviewed_sha` 与输入一致，`findings` 含文件路径与发现说明。摘要用于评论。样例只提供检查项，业务评分标准由内网 Skill 定义。
- 文档输入为 `MergedCommit`。首轮的 `needs_human` 提交问题，人工回复通过原事件快照和本轮 `input_summary` 送回同一 Session。成功结果的 `output` 为 `DocumentSuggestion`，包含 `commit_sha`、`document` 和 `suggestion`，提交 SHA 必须匹配。文档名为建议的显示文本，当前存储结构化产出。

## 工作流字段

- `trigger` 支持 `manual`、秒级 `poll` 和 `cron`。Cron 使用五段表达式与 IANA 时区。
- `source` 指定 `connector`、`operation` 和对应选项。`condition` 为手动任务的 `always`，或扫描任务的 `new_item`。
- `agent` 绑定注册的 Profile 和 Skill ID/版本。`session` 选择 `per_task` 或按来源主题键使用 `per_subject`。`result` 把运行结果映射到已注册动作，如 `publish_review` 或 `save_document_suggestion`。
- 默认目录为 `workflows/`，可用 `ALWAYS_ON_WORKFLOWS_DIR` 覆盖，配置在服务启动时加载。

验收用例见 `tests/test_workflows.py`，本机演示见 [运行手册](operations.md)。
