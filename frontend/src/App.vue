<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { api, label, type Action, type Task, type TaskSummary } from './api'
import AgentCenter from './AgentCenter.vue'
import SessionCenter from './SessionCenter.vue'
import TaskDetail from './TaskDetail.vue'
import TaskLobby from './TaskLobby.vue'
import WorkflowCenter from './WorkflowCenter.vue'

const query = new URLSearchParams(location.search)
const views = { tasks: '任务大厅', sessions: '会话中心', workflows: '工作流管理', agents: 'Agent 管理', actions: '外部动作' } as const
type View = keyof typeof views
function readView(query: URLSearchParams): View {
  const name = query.get('view')
  if (name === 'sessions' || name === 'workflows' || name === 'agents' || name === 'actions') return name
  return 'tasks'
}
const view = ref<View>(readView(query))
const selected = ref(query.get('task'))
const selectedSession = ref(query.get('session'))
const selectedWorkflow = ref(query.get('workflow'))
const selectedAgent = ref(query.get('agent'))
const tasks = ref<TaskSummary[]>([])
const task = ref<Task | null>(null)
const actions = ref<Action[]>([])
const actionStatus = ref('all')
const actionSearch = ref('')
const title = ref('')
const prompt = ref('')
const busy = ref(false)
const loaded = ref(false)
const online = ref(false)
const mode = ref('mock')
const runtimeLabels: Record<string, string> = { mock: '模拟模式', mixed: 'SDD 使用 Claude', 'claude-smoke': 'Claude 测试' }
const error = ref('')
const loadError = ref(false)
const taskError = ref('')
const feedback = ref('')
const createDialog = ref<HTMLDialogElement | null>(null)
const actionDialog = ref<HTMLDialogElement | null>(null)
const closeDialog = ref<HTMLDialogElement | null>(null)
const closeTarget = ref<Task | null>(null)
const closeReason = ref('')
const reconciling = ref<Action | null>(null)
const decision = ref('delivered')
const evidence = ref('')
const remoteId = ref('')
let refreshVersion = 0
let timer: number | undefined

const actionStates = computed(() => [...new Set(actions.value.map(item => item.status))].sort())

function actionTitle(kind: string) {
  if (kind === 'human_request') return '人工待办通知'
  if (kind === 'mr_review') return 'MR 评审评论'
  return kind
}

const visibleActions = computed(() => {
  const needle = actionSearch.value.trim().toLocaleLowerCase()
  return actions.value.filter(action =>
    (actionStatus.value === 'all' || action.status === actionStatus.value) &&
    [actionTitle(action.kind), action.connector, action.business_key, action.remote_id]
      .some(text => text?.toLocaleLowerCase().includes(needle)),
  )
})
function storeLocation(push = false) {
  const next = new URL(location.href)
  if (view.value !== 'tasks') next.searchParams.set('view', view.value)
  else next.searchParams.delete('view')
  if (view.value === 'tasks' && selected.value) next.searchParams.set('task', selected.value)
  else next.searchParams.delete('task')
  if (selectedSession.value) next.searchParams.set('session', selectedSession.value)
  else next.searchParams.delete('session')
  if (selectedWorkflow.value) next.searchParams.set('workflow', selectedWorkflow.value)
  else next.searchParams.delete('workflow')
  if (selectedAgent.value) next.searchParams.set('agent', selectedAgent.value)
  else next.searchParams.delete('agent')
  if (next.href === location.href) return
  if (push) history.pushState(null, '', next)
  else history.replaceState(null, '', next)
}

function setTask(id: string | null) {
  if (selected.value !== id) {
    selected.value = id
    task.value = null
    taskError.value = ''
    feedback.value = ''
    error.value = ''
  }
}

function navigate(next: View) {
  view.value = next
  error.value = ''
  storeLocation(true)
  window.scrollTo(0, 0)
  void refresh()
}

function openTask(id: string) {
  setTask(id)
  navigate('tasks')
}

function openLobby() {
  setTask(null)
  navigate('tasks')
}

function openSession(id: string) {
  selectedSession.value = id
  navigate('sessions')
}

function openWorkflow(id: string) {
  selectedWorkflow.value = id
  navigate('workflows')
}

function openAgent(id: string) {
  selectedAgent.value = id
  navigate('agents')
}

function restoreLocation() {
  const current = new URLSearchParams(location.search)
  view.value = readView(current)
  setTask(current.get('task'))
  selectedSession.value = current.get('session')
  selectedWorkflow.value = current.get('workflow')
  selectedAgent.value = current.get('agent')
  error.value = ''
  void refresh()
}

async function refresh() {
  const version = ++refreshVersion
  try {
    const [list, health, externalActions] = await Promise.all([
      api<TaskSummary[]>('/tasks'), api<{ mode: string; worker_alive: boolean }>('/health'), api<Action[]>('/actions'),
    ])
    if (version !== refreshVersion) return
    tasks.value = list
    actions.value = externalActions
    online.value = health.worker_alive
    mode.value = health.mode
    loadError.value = false
    const id = view.value === 'tasks' ? selected.value : null
    if (id) {
      if (!list.some(item => item.id === id)) {
        task.value = null
        taskError.value = '未找到这项任务。'
      } else {
        const detail = await api<Task>('/tasks/' + id).catch((failure: Error) => {
          if (version === refreshVersion) taskError.value = failure.message
          return null
        })
        if (version === refreshVersion && detail) {
          const opening = task.value === null
          task.value = detail
          taskError.value = ''
          if (opening) void nextTick(() => document.getElementById('task-heading')?.focus({ preventScroll: true }))
        }
      }
    }
  } catch {
    if (version === refreshVersion) {
      online.value = false
      loadError.value = true
    }
  } finally {
    if (version === refreshVersion) loaded.value = true
  }
}

async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  feedback.value = ''
  try {
    await action()
    await refresh()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '操作失败，请重试。'
  } finally {
    busy.value = false
  }
}

function createTask() {
  void perform(async () => {
    const item = await api<{ id: string }>('/tasks', { title: title.value, prompt: prompt.value })
    title.value = ''
    prompt.value = ''
    createDialog.value?.close()
    openTask(item.id)
  })
}

function sendReply(request: string, answer: string) {
  void perform(async () => {
    await api('/requests/' + request + '/reply', { answer })
    feedback.value = '回复已提交，原会话将继续。'
  })
}

function resumeTask(session: string, instruction: string) {
  void perform(async () => {
    await api('/sessions/' + session + '/resume', { instruction })
    feedback.value = '补充指令已排队，将在原会话执行。'
  })
}

function confirmClose(target: Task) {
  closeTarget.value = target
  closeReason.value = ''
  error.value = ''
  closeDialog.value?.showModal()
}

function closeTask() {
  const target = closeTarget.value
  if (!target) return
  void perform(async () => {
    await api('/tasks/' + target.id + '/close', { reason: closeReason.value.trim() || null })
    closeDialog.value?.close()
  })
}

function recover(run: string, operation: 'stop' | 'retry') {
  void perform(async () => {
    await api('/runs/' + run + '/recovery', { operation })
  })
}

function openReconciliation(action: Action) {
  reconciling.value = action
  decision.value = 'delivered'
  evidence.value = ''
  remoteId.value = ''
  error.value = ''
  actionDialog.value?.showModal()
}

function reconcile() {
  const action = reconciling.value
  if (!action) return
  void perform(async () => {
    await api('/actions/' + action.id + '/reconciliation', {
      decision: decision.value, evidence: evidence.value,
      remote_id: decision.value === 'delivered' ? remoteId.value : null,
    })
    actionDialog.value?.close()
  })
}

onMounted(() => {
  window.addEventListener('popstate', restoreLocation)
  void refresh()
  timer = window.setInterval(() => { if (!busy.value) void refresh() }, 1500)
})
onUnmounted(() => {
  ++refreshVersion
  window.clearInterval(timer)
  window.removeEventListener('popstate', restoreLocation)
})
</script>

<template>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand"><span class="brand-mark" aria-hidden="true"></span><strong>Always-on</strong></div>
      <nav aria-label="管理台">
        <button :aria-current="view === 'tasks' ? 'page' : undefined" @click="openLobby">任务大厅</button>
        <button :aria-current="view === 'sessions' ? 'page' : undefined" @click="navigate('sessions')">会话中心</button>
        <button :aria-current="view === 'workflows' ? 'page' : undefined" @click="navigate('workflows')">工作流管理</button>
        <button :aria-current="view === 'agents' ? 'page' : undefined" @click="navigate('agents')">Agent 管理</button>
        <button :aria-current="view === 'actions' ? 'page' : undefined" @click="navigate('actions')">外部动作<span v-if="actions.some(item => item.status === 'needs_reconcile')" class="nav-alert"></span></button>
      </nav>
      <span class="connection"><i :class="{ online }"></i>{{ online ? `${runtimeLabels[mode] ?? mode} · 服务在线` : loaded ? '服务未连接' : '正在连接服务' }}</span>
    </div>
  </header>

  <main class="frame">
    <div v-if="view !== 'tasks' || !selected" class="page-heading">
      <h1>{{ views[view] }}</h1>
      <button v-if="view === 'tasks'" class="primary" :disabled="!online" @click="error = ''; createDialog?.showModal()">新建 SDD 任务</button>
    </div>
    <p v-if="loadError" class="error" role="status">暂时无法刷新管理台。连接恢复后会自动重试。</p>
    <p v-if="error && !(view === 'tasks' && task) && !createDialog?.open && !actionDialog?.open && !closeDialog?.open" class="error" role="alert">{{ error }}</p>

    <TaskLobby v-show="view === 'tasks' && !selected" :tasks="tasks" :loaded="loaded && (!loadError || !!tasks.length)" @task="openTask" />

    <template v-if="view === 'tasks' && selected">
      <TaskDetail v-if="task" :key="task.id" :task="task" :busy="busy" :online="online" :feedback="feedback"
        :operation-error="closeDialog?.open ? '' : error"
        @back="openLobby" @close="confirmClose" @reply="sendReply" @resume="resumeTask" @recover="recover" @session="openSession" @workflow="openWorkflow" />
      <section v-else class="empty-board" aria-live="polite">
        <h1>{{ taskError || '正在读取任务' }}</h1>
        <button @click="openLobby">返回任务大厅</button>
      </section>
    </template>

    <SessionCenter v-else-if="view === 'sessions'" :initial-id="selectedSession" :online="online"
      @select="id => { selectedSession = id; storeLocation() }" @task="openTask" />

    <WorkflowCenter v-else-if="view === 'workflows'" :initial-id="selectedWorkflow" :online="online"
      @select="id => { selectedWorkflow = id; storeLocation() }" @task="openTask" @agent="openAgent" />

    <AgentCenter v-else-if="view === 'agents'" :initial-id="selectedAgent" :online="online"
      @select="id => { selectedAgent = id; storeLocation() }" @workflow="openWorkflow" />

    <section v-else-if="view === 'actions'" class="panel flat-panel" aria-label="外部动作记录">
      <div class="list-tools action-tools">
        <label class="field search">搜索动作<input v-model="actionSearch" type="search" placeholder="动作、来源或记录 ID" /></label>
        <label class="field">状态
          <select v-model="actionStatus" aria-label="状态">
            <option value="all">全部状态</option>
            <option v-for="status in actionStates" :key="status" :value="status">{{ label(status) }}</option>
          </select>
        </label>
      </div>
      <p v-if="!visibleActions.length" class="queue-empty">{{ !loaded ? '正在读取动作…' : actions.length ? '没有符合筛选条件的动作。' : '暂无外部动作记录。' }}</p>
      <div v-else class="action-list">
        <article v-for="action in visibleActions" :key="action.id" class="action-row">
          <div>
            <h3>{{ actionTitle(action.kind) }}</h3>
            <p class="muted">{{ action.connector }} · 已尝试 {{ action.attempts }} 次</p>
            <p v-if="action.detail" class="prose">{{ action.detail }}</p>
            <details class="disclosure compact-disclosure">
              <summary>动作详情</summary>
              <dl class="facts inline-facts">
                <dt>业务键</dt><dd class="mono">{{ action.business_key }}</dd>
                <template v-if="action.remote_id"><dt>远端记录</dt><dd class="mono">{{ action.remote_id }}</dd></template>
              </dl>
            </details>
          </div>
          <div class="action-controls">
            <span class="chip" :data-status="action.status">{{ label(action.status) }}</span>
            <button v-if="action.status === 'needs_reconcile'" :disabled="!online" @click="openReconciliation(action)">登记核对结果</button>
          </div>
        </article>
      </div>
    </section>
  </main>

  <dialog ref="createDialog" aria-labelledby="create-heading" @close="error = ''">
    <form @submit.prevent="createTask">
      <h2 id="create-heading">新建 SDD 任务</h2>
      <label class="field">任务名称<input v-model="title" required maxlength="300" autofocus /></label>
      <label class="field">任务说明<textarea v-model="prompt" required maxlength="12000" rows="5" /></label>
      <p v-if="mode === 'mixed'" class="muted small">任务内容会发送到已配置的模型网关。请使用可发给该网关的内容。</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="dialog-buttons">
        <button type="button" @click="createDialog?.close()">取消</button>
        <button class="primary" :disabled="busy || !online || !title.trim() || !prompt.trim()">提交任务</button>
      </div>
    </form>
  </dialog>

  <dialog ref="closeDialog" aria-labelledby="close-heading" @close="error = ''">
    <form @submit.prevent="closeTask">
      <h2 id="close-heading">关闭任务</h2>
      <p class="prose">{{ closeTarget?.title }}</p>
      <p>确认后将取消待执行的唤醒，任务停止接收新的运行和人工回复。已经开始的轮次将收尾。</p>
      <label class="field">关闭原因（可选）<input v-model="closeReason" maxlength="300" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="dialog-buttons">
        <button type="button" @click="closeDialog?.close()">返回</button>
        <button class="danger" :disabled="busy || !online">确认关闭任务</button>
      </div>
    </form>
  </dialog>

  <dialog ref="actionDialog" aria-labelledby="reconcile-heading" @close="error = ''">
    <form @submit.prevent="reconcile">
      <h2 id="reconcile-heading">登记核对结果</h2>
      <label class="field">远端状态<select v-model="decision"><option value="delivered">已确认送达</option><option value="absent">已确认远端未接收</option></select></label>
      <label v-if="decision === 'delivered'" class="field">远端记录 ID<input v-model="remoteId" required maxlength="300" /></label>
      <label class="field">核对依据<textarea v-model="evidence" required maxlength="12000" rows="3" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="dialog-buttons">
        <button type="button" @click="actionDialog?.close()">取消</button>
        <button class="primary" :disabled="busy || !online">保存核对结果</button>
      </div>
    </form>
  </dialog>
</template>
