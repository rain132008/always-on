interface TaskIdentity {
  id: string
  title: string
  status: string
  updated_at: number
  workflow_id: string
}

export interface TaskSummary extends TaskIdentity {
  workflow_enabled: number
  latest_summary: string | null
  pending_question: string | null
  next_wakeup_at: number | null
}

export interface Run {
  id: string
  generation: number
  status: string
  resume_id: string | null
  pid: number | null
  summary: string | null
  created_at: number
  finished_at: number | null
  exit_code: number | null
  agent_id: string | null
  agent_name: string | null
  runner: string | null
}

export interface HumanRequest {
  id: string
  question: string
  answer: string | null
  created_at: number
  answered_at: number | null
}

export interface Task extends TaskIdentity {
  prompt: string
  workflow_enabled: boolean
  next_wakeup: { due_at: number } | null
  session: { id: string; name: string; external_id: string | null; workspace: string }
  runs: Run[]
  requests: HumanRequest[]
  events: Array<{ id: number; kind: string; summary: string; created_at: number }>
  artifacts: Artifact[]
}

export interface SessionSummary {
  id: string
  name: string
  external_id: string | null
  task_id: string
  task_title: string
  task_status: string
  updated_at: number
  workflow_enabled: number
  workflow_id: string
}

export interface SessionDetail extends SessionSummary {
  workspace: string
  runs: Run[]
}

export interface WorkflowSummary {
  id: string
  name: string
  enabled: boolean
  runner: string
  selected_agent: Pick<AgentProfile, 'id' | 'name'>
  trigger: { type: 'manual' } | { type: 'poll'; seconds: number } |
    { type: 'cron'; expression: string; timezone: string }
}

export interface WorkflowDetail extends WorkflowSummary {
  yaml: string
  runs: Array<Pick<Run, 'id' | 'status' | 'summary' | 'exit_code' | 'generation' | 'created_at' | 'agent_name' | 'runner'> &
    { task_id: string; task_title: string }>
  scans: Array<{
    id: number; trigger: string; status: string; seen: number | null; reason: string | null; started_at: number
  }>
}

export interface AgentProfile {
  id: string
  name: string
  description: string
  prompt: string
  updated_at: number
}

type Artifact = {
  id: string
  run_id: string
  created_at: number
} & (
  | { kind: 'mr_review'; content: { head_sha: string; summary: string; findings: Array<{ path: string; detail: string }> } }
  | { kind: 'document_suggestion'; content: { commit_sha: string; document: string; suggestion: string } }
)

export interface Action {
  id: string
  connector: string
  kind: string
  business_key: string
  status: string
  attempts: number
  detail: string | null
  remote_id: string | null
}

export async function api<T>(path: string, body?: object): Promise<T> {
  const response = await fetch('/api' + path, {
    cache: 'no-store',
    ...(body && { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`)
  }
  return response.json()
}

export const statusLabels: Record<string, string> = {
  ready: '已排队', running: '运行中', waiting_human: '待人工回复', needs_human: '已请求人工',
  waiting_timer: '等待定时唤醒', schedule_wakeup: '已预约唤醒',
  completed: '已完成', failed: '失败', recovery_required: '待恢复', interrupted: '已中断',
  pending: '待发送', sending: '发送中', sent: '已送达', reconciling: '核对中', needs_reconcile: '待人工核对',
  superseded: '已跳过旧版本',
  closed: '已关闭', cancelled: '已取消', enabled: '已启用', disabled: '已停用',
}

export const workflowLabels: Record<string, string> = {
  'sdd-task': 'SDD 通用任务', 'mr-review': 'MR 代码评审', 'doc-maintenance': '文档维护',
}

export const eventLabels: Record<string, string> = {
  task_created: '任务已提交', human_replied: '收到人工回复',
  agent_wakeup: 'Agent 已预约下次唤醒',
  manual_resume: '手动继续会话', task_closed: '任务已关闭',
  mr_head: '发现 MR 版本', merged_commit: '发现已合并提交',
}

export function label(status: string) {
  return statusLabels[status] ?? status
}

export function runnerLabel(runner: string) {
  if (runner === 'mock') return 'Mock'
  if (runner === 'claude' || runner === 'claude-smoke') return 'Claude Code 2.1.109'
  return runner
}

export function resumeRestriction(status: string, enabled: boolean, externalId: string | null) {
  if (status === 'closed') return '关联任务已关闭。'
  if (!enabled) return '关联工作流已停用。'
  if (status === 'waiting_human') return '请在关联任务中回复人工待办。'
  if (status === 'waiting_timer') return '本会话已预约定时唤醒。'
  if (status === 'recovery_required') return '请在关联任务中处理待恢复运行。'
  if (!externalId) return '首次运行后登记 Session ID。'
  if (status !== 'completed' && status !== 'failed') return '请等当前运行结束。'
  return ''
}

const timestampFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
})

export function date(value: number) {
  return timestampFormat.format(new Date(value * 1000))
}

export function timestamp(value: number) {
  return new Date(value * 1000).toISOString()
}
