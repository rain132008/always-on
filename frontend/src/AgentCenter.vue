<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, date, type AgentProfile, type WorkflowSummary } from './api'

const props = defineProps<{ initialId: string | null; online: boolean }>()
const emit = defineEmits<{ select: [id: string | null]; workflow: [id: string] }>()
const agents = ref<AgentProfile[]>([])
const workflows = ref<WorkflowSummary[]>([])
const selected = ref<string | null>(props.initialId)
const creating = ref(false)
const draft = ref({ name: '', description: '', prompt: '' })
const filter = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const loaded = ref(false)

const visible = computed(() => {
  const needle = filter.value.trim().toLocaleLowerCase()
  return agents.value.filter(item =>
    [item.name, item.id].some(value => value.toLocaleLowerCase().includes(needle)),
  )
})
const current = computed(() => agents.value.find(item => item.id === selected.value))
const linked = computed(() => workflows.value.filter(workflow => workflow.selected_agent.id === selected.value))
const canSave = computed(() => {
  if (!draft.value.name.trim() || !draft.value.prompt.trim()) return false
  if (creating.value) return true
  const agent = current.value
  return !!agent && (
    draft.value.name.trim() !== agent.name ||
    draft.value.description.trim() !== agent.description ||
    draft.value.prompt.trim() !== agent.prompt
  )
})

function select(agent: AgentProfile | undefined) {
  selected.value = agent?.id ?? null
  creating.value = !agent
  draft.value = agent
    ? { name: agent.name, description: agent.description, prompt: agent.prompt }
    : { name: '', description: '', prompt: '' }
  error.value = ''
  notice.value = ''
  emit('select', selected.value)
}

async function refresh(preferred = selected.value) {
  const [catalog, bindings] = await Promise.all([api<AgentProfile[]>('/agents'), api<WorkflowSummary[]>('/workflows')])
  agents.value = catalog
  workflows.value = bindings
  const agent = catalog.find(item => item.id === preferred) ?? catalog[0]
  if (agent) select(agent)
}

async function load() {
  busy.value = true
  error.value = ''
  try {
    await refresh()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '无法读取 Agent。'
  } finally {
    busy.value = false
    loaded.value = true
  }
}

async function save() {
  if (busy.value || !canSave.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const url = creating.value ? '/agents' : '/agents/' + encodeURIComponent(selected.value ?? '')
    const result = await api<{ id: string }>(url, draft.value)
    filter.value = ''
    await refresh(result.id)
    notice.value = 'Agent 配置已保存。'
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '保存失败，请重试。'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <p v-if="error" class="error" role="alert">{{ error }} <button v-if="!agents.length && !creating" class="link" :disabled="busy" @click="load">重新读取</button></p>
  <div class="desk">
    <aside class="queue" aria-label="Agent 列表">
      <div class="list-tools">
        <div class="section-heading">
          <h3>Agent 列表</h3>
          <button class="quiet-button" :disabled="busy || !online" @click="select(undefined)">新建 Agent</button>
        </div>
        <label class="field search">搜索 Agent<input v-model="filter" type="search" placeholder="名称或 ID" /></label>
      </div>
      <p v-if="!visible.length" class="queue-empty">{{ !loaded ? '正在读取 Agent…' : agents.length ? '没有匹配的 Agent。' : '暂无 Agent。' }}</p>
      <div v-else class="queue-scroll">
        <button v-for="agent in visible" :key="agent.id" class="queue-item" :disabled="busy"
          :aria-current="selected === agent.id ? 'true' : undefined" @click="select(agent)">
          <strong>{{ agent.name }}</strong>
          <span v-if="agent.description" class="queue-meta">{{ agent.description }}</span>
        </button>
      </div>
    </aside>
    <section v-if="creating || current" class="task-detail" aria-label="Agent 详情">
      <header class="task-heading">
        <div>
          <h2>{{ creating ? '新建 Agent' : current?.name }}</h2>
          <p v-if="current" class="task-id mono">{{ current.id }}</p>
        </div>
        <time v-if="current">{{ date(current.updated_at) }}</time>
      </header>
      <div class="detail-columns agent-layout">
        <section class="panel">
          <h3>Agent 配置</h3>
          <form class="management-form" @submit.prevent="save">
            <label class="field">名称<input v-model="draft.name" required maxlength="300" /></label>
            <label class="field">职责说明<input v-model="draft.description" maxlength="600" /></label>
            <label class="field">任务提示词<textarea v-model="draft.prompt" rows="7" required maxlength="12000" /></label>
            <p class="muted small">真实 Runner 会将任务提示词与平台提示词一同传入 Agent。</p>
            <button class="primary" :disabled="busy || !online || !canSave">{{ creating ? '创建 Agent' : '保存配置' }}</button>
          </form>
          <p v-if="notice" class="muted feedback" role="status">{{ notice }}</p>
        </section>
        <section v-if="current" class="panel">
          <h3>绑定的工作流</h3>
          <p v-if="!linked.length" class="muted">可在工作流的「Agent 配置」中选择该 Agent。</p>
          <ul v-else class="run-list">
            <li v-for="workflow in linked" :key="workflow.id" class="run-heading">
              <button class="link" @click="emit('workflow', workflow.id)">{{ workflow.name }}</button>
              <span class="chip" :data-status="workflow.enabled ? 'enabled' : 'disabled'">{{ workflow.enabled ? '已启用' : '已停用' }}</span>
            </li>
          </ul>
        </section>
      </div>
    </section>
    <section v-else class="empty-board"><h2>{{ loaded ? '选择或新建 Agent' : '正在读取 Agent' }}</h2></section>
  </div>
</template>
