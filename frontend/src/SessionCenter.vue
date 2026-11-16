<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, date, label, resumeRestriction, type SessionDetail, type SessionSummary } from './api'
import ExpandableText from './ExpandableText.vue'

const props = defineProps<{ initialId: string | null; online: boolean }>()
const emit = defineEmits<{ select: [id: string | null]; task: [id: string] }>()
const sessions = ref<SessionSummary[]>([])
const selected = ref(props.initialId)
const current = ref<SessionDetail | null>(null)
const search = ref('')
const status = ref('all')
const instruction = ref('')
const name = ref('')
const editing = ref(false)
const busy = ref(false)
const notice = ref('')
const error = ref('')
const loadError = ref('')
const loaded = ref(false)
let timer: number | undefined
let revision = 0

const statusOptions = computed(() => [...new Set(sessions.value.map(item => item.task_status))].sort())
const visible = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase()
  return sessions.value.filter(session => (status.value === 'all' || session.task_status === status.value) &&
    [session.name, session.task_title, session.external_id, session.id]
    .some(value => value?.toLocaleLowerCase().includes(needle)))
})
watch([search, status], () => {
  if (!visible.value.some(session => session.id === selected.value)) selectSession(visible.value[0]?.id ?? null)
})

const resumeMessage = computed(() => {
  const session = current.value
  if (!session) return ''
  return resumeRestriction(session.task_status, Boolean(session.workflow_enabled), session.external_id)
})

async function refresh() {
  const token = ++revision
  try {
    const list = await api<SessionSummary[]>('/sessions')
    if (token !== revision) return
    sessions.value = list
    if (!list.some(item => item.id === selected.value)) {
      selected.value = visible.value[0]?.id ?? null
      emit('select', selected.value)
    }
    const detail = selected.value ? await api<SessionDetail>('/sessions/' + selected.value) : null
    if (token === revision) {
      current.value = detail
      loadError.value = ''
    }
  } catch (failure) {
    if (token === revision) loadError.value = failure instanceof Error ? failure.message : '无法读取会话。'
  } finally {
    if (token === revision) loaded.value = true
  }
}

function selectSession(id: string | null) {
  if (id === selected.value) return
  selected.value = id
  current.value = null
  instruction.value = ''
  editing.value = false
  notice.value = ''
  error.value = ''
  emit('select', id)
  void refresh()
}

async function save(action: 'name' | 'resume') {
  const session = current.value
  if (!session || busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const body = action === 'name' ? { name: name.value } : { instruction: instruction.value }
    await api('/sessions/' + session.id + '/' + action, body)
    if (selected.value === session.id) {
      if (action === 'name') editing.value = false
      else instruction.value = ''
      notice.value = action === 'name' ? '会话名称已保存。' : '继续指令已排队。'
    }
    await refresh()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '操作失败，请重试。'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(() => { if (!busy.value) void refresh() }, 1500)
})
onUnmounted(() => { ++revision; window.clearInterval(timer) })
</script>

<template>
  <p v-if="error || loadError" class="error" role="alert">{{ error || loadError }}</p>
  <div class="desk">
    <aside class="queue" aria-label="会话列表">
      <div class="list-tools">
        <div class="section-heading"><h3>会话列表</h3><span v-if="loaded" class="list-count">{{ visible.length }} 项</span></div>
        <label class="field search">搜索会话<input v-model="search" type="search" placeholder="名称、任务或 Session ID" /></label>
        <label class="field">关联任务状态
          <select v-model="status" aria-label="关联任务状态">
            <option value="all">全部状态</option>
            <option v-for="option in statusOptions" :key="option" :value="option">{{ label(option) }}</option>
          </select>
        </label>
      </div>
      <p v-if="!visible.length" class="queue-empty">{{ !loaded ? '正在读取会话…' : sessions.length ? '没有符合筛选条件的会话。' : '暂无会话。' }}</p>
      <div v-else class="queue-scroll">
        <button v-for="session in visible" :key="session.id" class="queue-item" :aria-current="selected === session.id ? 'true' : undefined" @click="selectSession(session.id)">
          <span class="queue-title"><i class="status-dot" :data-status="session.task_status"></i><strong>{{ session.name }}</strong></span>
          <span class="queue-meta"><time>{{ date(session.updated_at) }}</time><span class="chip" :data-status="session.task_status">{{ label(session.task_status) }}</span></span>
        </button>
      </div>
    </aside>
    <section v-if="current" class="task-detail" aria-label="会话详情">
      <header class="task-heading">
        <div>
          <h2>{{ current.name }}</h2>
          <button class="link" @click="emit('task', current.task_id)">打开关联任务 · {{ current.task_title }}</button>
        </div>
        <span class="chip" :data-status="current.task_status">{{ label(current.task_status) }}</span>
      </header>
      <div class="detail-columns">
        <section class="panel management-panel">
          <h3>会话信息</h3>
          <dl class="facts inline-facts">
            <dt>Session ID</dt><dd class="mono">{{ current.external_id || '待登记' }}</dd>
          </dl>
          <details class="disclosure compact-disclosure">
            <summary>运行环境</summary>
            <dl class="facts inline-facts"><dt>工作区</dt><dd class="mono">{{ current.workspace }}</dd></dl>
          </details>
          <form v-if="editing" class="management-form" @submit.prevent="save('name')">
            <label class="field">会话名称<input v-model="name" required maxlength="300" /></label>
            <div class="button-row">
              <button class="primary" :disabled="busy || !online || !name.trim()">保存名称</button>
              <button type="button" @click="editing = false">取消</button>
            </div>
          </form>
          <button v-else class="quiet-button spaced-button" @click="name = current.name; editing = true">修改名称</button>
        </section>
        <section class="panel management-panel">
          <h3>手动继续会话</h3>
          <p v-if="resumeMessage" class="muted">{{ resumeMessage }}</p>
          <form v-else class="management-form" @submit.prevent="save('resume')">
            <label class="field">补充指令<textarea v-model="instruction" rows="3" maxlength="12000" required /></label>
            <button class="primary" :disabled="!online || busy || !instruction.trim()">继续原会话</button>
          </form>
          <p v-if="notice" class="muted feedback" role="status">{{ notice }}</p>
        </section>
      </div>
      <section class="panel">
        <h3>运行历史</h3>
        <p v-if="!current.runs.length" class="muted">暂无运行记录。</p>
        <ol v-else class="run-list">
          <li v-for="run in current.runs" :key="run.id">
            <div class="run-heading"><strong>第 {{ run.generation }} 轮</strong><span class="chip" :data-status="run.status">{{ label(run.status) }}</span></div>
            <ExpandableText v-if="run.summary" :text="run.summary" />
            <div class="run-meta"><time>{{ date(run.finished_at ?? run.created_at) }}</time><a class="link" :href="'/api/runs/' + run.id + '/log'" target="_blank" rel="noopener">查看日志</a></div>
          </li>
        </ol>
      </section>
    </section>
    <section v-else class="empty-board"><h2>{{ selected || !loaded ? '正在读取会话' : sessions.length ? '没有匹配的会话' : '暂无会话可查看' }}</h2></section>
  </div>
</template>
