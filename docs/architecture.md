# 当前架构

- `backend/api.py` 提供 FastAPI HTTP 入口和 Vue 3 管理台静态资源。管理台通过 API 创建与关闭任务、回复待办、查找和恢复会话、启停工作流，以及处理恢复和人工核对。
- `backend/service.py` 管理单服务锁、SQLite 和一个 Dispatcher 线程。Dispatcher 领取 Wakeup、串行执行 Agent，并处理 Outbox。SQLite 使用 WAL，初始 Schema 由 Alembic 建立。
- `backend/tasks.py` 管理 Task、Event、Wakeup、Session、Run 和人工待办。人工回复先落库再排队。RunResult 通过代次核验后，在一个事务中更新状态、归档 Wakeup 并保存延期预约或待办。
- `backend/runner.py` 管理子进程启动、PID 核对、超时、接续和恢复。`backend/agent.py` 提供 Mock 与固定版 Claude CLI 的合成任务验收入口。`backend/workspace.py` 管理工作区身份，`backend/contracts.py` 提供结构化契约。
- `backend/outbox.py` 管理副作用状态和动作审计；`backend/connectors/mock.py` 用独立 SQLite 文件模拟远端动作落地与核对。
- `backend/workflow.py` 在启动时读取 YAML，绑定已注册的数据源、Profile 和结果动作，并管理持久化启停与扫描历史。`backend/agent_catalog.py` 管理 SQLite 中的 Agent 配置和每个工作流的执行 Agent 绑定。工作流入队事务解析绑定并保存至事件快照；快照随 Wakeup 进入 Runner，使重启和人工回复沿用原 Agent、Skill 版本及输入。
- `backend/connectors/git.py` 定义 Git 端口；`backend/connectors/mock_git.py` 用样例数据和独立 SQLite 实现事件、合并增量和评论；`backend/git_workflows.py` 把 Git 数据映射为工作流输入，并把已校验的结果转为产出或 Outbox 动作。

默认入口支持 SDD 手动任务、Mock MR 评审和 Mock 夜间文档维护。`start-local.ps1` 为同一入口启用真实 SDD Runner，Dispatcher 根据事件快照选择执行器。独立 Claude 验收入口继续使用隔离数据库、合成工作区及相同的会话与任务管理。参见 [实施计划](../implementPlan.md)。
