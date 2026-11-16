# Agent 运行协议 v1

Runner 每轮传入任务 ID、Run ID、执行代次、唤醒原因、任务说明、当前输入、自动唤醒剩余次数和可选的 Resume ID。结构定义位于 `backend/contracts.py`。真实 Claude CLI 验收入口显式注入 [平台系统提示词](platform-prompt.md)，Mock 使用同一输出契约。

结果须回传同一任务、Run、执行代次和协议版本。首次返回外部 Session ID，后续报告使用传入的 Resume ID。服务端核对身份和状态后持久化结果，外部动作、任务状态和 Wakeup 由服务处理。

| 结果 | 必需字段 | 平台行为 |
| --- | --- | --- |
| `completed` / `failed` | `summary` | 保存本轮结果。 |
| `needs_human` | `summary`、`question` | 生成待办，收到回复后通过 `human_replied` 唤醒原 Session。 |
| `schedule_wakeup` | `summary`、`after_seconds` | 按结果入库时间加上 5 至 604800 的整数秒，保存下一次 Wakeup。到期后通过 `agent_wakeup` 唤醒原 Session。 |

每轮只选择一种结果，延期和人工问题互斥。同一 Run 只能有效提交一次。连续等待最多自动唤醒 5 次，新的人工回复或手动继续开启新的计数。Task 关闭取消预约。工作流停用时暂停领取，到期的预约在启用后可以补跑。

平台要求提供可交接的摘要和基于证据的文档维护建议。每轮摘要、事件和 Run 持久化。现有业务文档流程保存结构化建议，文件产物引用、真实文档写入和审批待内网适配。

扫描工作流还会附带 `workflow` 快照，包含工作流 ID、配置摘要、Profile、Skill ID/版本、Connector、结果动作和来源输入。评审及文档建议的扩展字段位于 `output`，分别由 `ReviewOutput` 和 `DocumentSuggestion` 校验。具体格式见 [Connector 与工作流契约](../docs/connector-contracts.md)。
