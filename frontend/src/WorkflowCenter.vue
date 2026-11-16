<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, date, label, runnerLabel, type AgentProfile, type WorkflowDetail, type WorkflowSummary } from './api'
import ExpandableText from './ExpandableText.vue'

const props = defineProps<{ initialId: string | null; online: boolean }>()
const emit = defineEmits<{ select: [id: string | null]; task: [id: string]; agent: [id: string] }>()
const workflows = ref<WorkflowSummary[]>([])
const selected = ref(props.initialId)
const current = ref<WorkflowDetail | null>(null)
const search = ref('')
const enabledFilter = ref('all')
const tab = ref<'history' | 'yaml' | 'agent'>('history')
const agents = ref<AgentProfile[]>([])
const agentChoice = ref('')
const notice = ref('')
const busy = ref(false)
const error = ref('')
const loadError = ref('')
const loaded = ref(false)
let timer: number | undefined
let revision = 0

const visible = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase()
  return workflows.value.filter(workflow =>
    (enabledFilter.value === 'all' || workflow.enabled === (enabledFilter.value === 'enabled')) &&
    [workflow.name, workflow.id].some(value => value.toLocaleLowerCase().includes(needle)),
  )
})
watch([search, enabledFilter], () => {
  if (!visible.value.some(workflow => workflow.id === selected.value)) selectWorkflow(visible.value[0]?.id ?? null)
})

function schedule(trigger: WorkflowSummary['trigger']) {
  if (trigger.type === 'manual') return '人工触发'
  if (trigger.type === 'poll') return '每 ' + trigger.seconds + ' 秒轮询'
  return trigger.expression + ' · ' + trigger.timezone
}

async function refresh() {
  const token = ++revision
  try {
    const list = await api<WorkflowSummary[]>('/workflows')
    if (token !== revision) return
    workflows.value = list
    if (!list.some(item => item.id === selected.value)) {
      selected.value = visible.value[0]?.id ?? null
      emit('select', selected.value)
    }
    const detail = selected.value ? await api<WorkflowDetail>('/workflows/' + encodeURIComponent(selected.value)) : null
    if (token === revision) {
      const previous = current.value
      if (!detail || !previous || detail.id !== previous.id || agentChoice.value === previous.selected_agent.id) {
        agentChoice.value = detail?.selected_agent.id ?? ''
      }
      current.value = detail
      loadError.value = ''
    }
  } catch (failure) {
    if (token === revision) loadError.value = failure instanceof Error ? failure.message : '无法读取工作流。'
  } finally {
    if (token === revision) loaded.value = true
  }
}

function selectWorkflow(id: string | null) {
  if (selected.value === id) return
  selected.value = id
  current.value = null
  agentChoice.value = ''
  error.value = ''
  notice.value = ''
  emit('select', id)
  void refresh()
}

async function showAgentConfig() {
  tab.value = 'agent'
  error.value = ''
  try {
    agents.value = await api<AgentProfile[]>('/agents')
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '无法读取 Agent 列表。'
  }
}

async function saveAgent() {
  const workflow = current.value
  if (!workflow || busy.value || !agentChoice.value || agentChoice.value === workflow.selected_agent.id) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await api('/workflows/' + encodeURIComponent(workflow.id) + '/agent', { agent_id: agentChoice.value })
    if (selected.value === workflow.id) notice.value = 'Agent 绑定已保存。'
    await refresh()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '保存失败，请重试。'
  } finally {
    busy.value = false
  }
}

async function toggle() {
  const workflow = current.value
  if (!workflow || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api('/workflows/' + encodeURIComponent(workflow.id) + '/control', { enabled: !workflow.enabled })
    await refresh()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '操作失败，请重试。'
  } finally {
    busy.value = false
  }
}

const scanTriggers: Record<string, string> = { scheduled: '定时扫描', manual: '手动扫描', result_refresh: '提交前核验' }
onMounted(() => {
  void refresh()
  timer = window.setInterval(() => { if (!busy.value) void refresh() }, 1500)
})
onUnmounted(() => { ++revision; window.clearInterval(timer) })
</script>

<template>
  <p v-if="error || loadError" class="error" role="alert">{{ error || loadError }}</p>
  <div class="desk">
    <aside class="queue" aria-label="工作流列表">
      <div class="list-tools">
        <div class="section-heading"><h3>工作流列表</h3><span v-if="loaded" class="list-count">{{ visible.length }} 项</span></div>
        <label class="field search">搜索工作流<input v-model="search" type="search" placeholder="工作流名称或 ID" /></label>
        <label class="field">状态
          <select v-model="enabledFilter" aria-label="状态">
            <option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option>
          </select>
        </label>
      </div>
      <p v-if="!visible.length" class="queue-empty">{{ !loaded ? '正在读取工作流…' : workflows.length ? '没有符合筛选条件的工作流。' : '暂无已加载的工作流。' }}</p>
      <div v-else class="queue-scroll">
        <button v-for="workflow in visible" :key="workflow.id" class="queue-item" :aria-current="selected === workflow.id ? 'true' : undefined" @click="selectWorkflow(workflow.id)">
          <span class="queue-title"><i class="status-dot" :data-status="workflow.enabled ? 'enabled' : 'disabled'"></i><strong>{{ workflow.name }}</strong></span>
          <span class="queue-meta"><span>{{ schedule(workflow.trigger) }}</span><span class="chip" :data-status="workflow.enabled ? 'enabled' : 'disabled'">{{ workflow.enabled ? '已启用' : '已停用' }}</span></span>
        </button>
      </div>
    </aside>
    <section v-if="current" class="task-detail" aria-label="工作流详情">
      <header class="task-heading">
        <div>
          <h2>{{ current.name }}</h2>
          <p class="task-id">{{ schedule(current.trigger) }} · {{ runnerLabel(current.runner) }}</p>
          <button class="link" @click="emit('agent', current.selected_agent.id)">Agent · {{ current.selected_agent.name }}</button>
        </div>
        <div class="button-row">
          <span class="chip" :data-status="current.enabled ? 'enabled' : 'disabled'" aria-live="polite">{{ current.enabled ? '已启用' : '已停用' }}</span>
          <button :disabled="busy || !online" @click="toggle">{{ current.enabled ? '停用工作流' : '启用工作流' }}</button>
        </div>
      </header>
      <p v-if="!current.enabled" class="panel muted">新触发已暂停。启用后会继续处理排队任务，并从原游标补扫。</p>
      <div class="subnav" aria-label="工作流详情视图">
        <button :aria-pressed="tab === 'history'" @click="tab = 'history'">运行历史</button>
        <button :aria-pressed="tab === 'agent'" @click="showAgentConfig">Agent 配置</button>
        <button :aria-pressed="tab === 'yaml'" @click="tab = 'yaml'">YAML 配置</button>
      </div>
      <section v-if="tab === 'agent'" class="panel">
        <h3>工作流 Agent</h3>
        <form class="management-form agent-binding-form" @submit.prevent="saveAgent">
          <label class="field">执行 Agent
            <select v-model="agentChoice" aria-label="执行 Agent" :disabled="busy || !agents.length">
              <option v-if="!agents.length" :value="current.selected_agent.id">{{ current.selected_agent.name }}</option>
              <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option>
            </select>
          </label>
          <p class="muted small">新触发会使用保存后的配置。已排队的任务和原会话续跑沿用原配置。</p>
          <div class="button-row">
            <button class="primary" :disabled="busy || !online || !agentChoice || agentChoice === current.selected_agent.id">保存 Agent 绑定</button>
            <button type="button" @click="emit('agent', agentChoice || current.selected_agent.id)">查看 Agent</button>
          </div>
        </form>
        <p v-if="notice" class="muted feedback" role="status">{{ notice }}</p>
      </section>
      <section v-else-if="tab === 'yaml'" class="panel yaml-panel">
        <p class="muted small">这是启动时加载的 YAML。页面保存的 Agent 绑定显示在「Agent 配置」中。</p>
        <pre class="log" aria-label="已加载的 YAML 配置">{{ current.yaml }}</pre>
      </section>
      <div v-else class="detail-columns">
        <section class="panel">
          <h3>任务运行</h3>
          <p v-if="!current.runs.length" class="muted">暂无任务运行。</p>
          <ol v-else class="run-list">
            <li v-for="run in current.runs" :key="run.id">
              <div class="run-heading"><button class="link" @click="emit('task', run.task_id)">{{ run.task_title }}</button><span class="chip" :data-status="run.status">{{ label(run.status) }}</span></div>
              <ExpandableText v-if="run.summary" :text="run.summary" />
              <div class="run-meta"><time>{{ date(run.created_at) }}</time><span class="muted small">{{ run.agent_name ? run.agent_name + ' · ' : '' }}第 {{ run.generation }} 轮<span v-if="run.exit_code !== null && run.exit_code !== 0"> · 退出码 {{ run.exit_code }}</span></span></div>
            </li>
          </ol>
        </section>
        <section class="panel">
          <h3>数据源扫描</h3>
          <p v-if="!current.scans.length" class="muted">暂无扫描记录。</p>
          <ol v-else class="run-list">
            <li v-for="scan in current.scans" :key="scan.id">
              <div class="run-heading"><strong>{{ scanTriggers[scan.trigger] ?? scan.trigger }}</strong><span class="chip" :data-status="scan.status">{{ label(scan.status) }}</span></div>
              <p v-if="scan.reason" class="prose" :data-status="scan.status">{{ scan.reason }}</p>
              <p v-else-if="scan.seen !== null" class="muted">读取到 {{ scan.seen }} 项变更</p>
              <time>{{ date(scan.started_at) }}</time>
            </li>
          </ol>
        </section>
      </div>
    </section>
    <section v-else class="empty-board"><h2>{{ selected || !loaded ? '正在读取工作流' : workflows.length ? '没有匹配的工作流' : '暂无工作流可查看' }}</h2></section>
  </div>
</template>
