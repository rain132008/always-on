<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { date, label, timestamp, workflowLabels, type TaskSummary } from './api'

const props = defineProps<{ tasks: TaskSummary[]; loaded: boolean }>()
const emit = defineEmits<{ task: [id: string] }>()
const search = ref('')
const status = ref('all')
const type = ref('all')
const limit = ref(18)
const groups = [
  { id: 'all', name: '全部', tone: '' },
  { id: 'waiting_human', name: '待我处理', tone: 'waiting_human' },
  { id: 'running', name: '运行中', tone: 'running' },
  { id: 'issues', name: '异常', tone: 'failed' },
  { id: 'waiting_timer', name: '已预约', tone: 'waiting_timer' },
  { id: 'completed', name: '已完成', tone: 'completed' },
  { id: 'ready', name: '排队中', tone: 'ready' },
  { id: 'closed', name: '已关闭', tone: 'closed' },
]
const priority: Record<string, number> = {
  waiting_human: 0, running: 1, recovery_required: 2, failed: 2, completed: 3, waiting_timer: 4, ready: 5, closed: 6,
}

function matches(task: TaskSummary, filter: string) {
  return filter === 'all' || (filter === 'issues' ? ['failed', 'recovery_required'].includes(task.status) : task.status === filter)
}

const types = computed(() => [...new Set(props.tasks.map(task => task.workflow_id))].sort())
const candidates = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase()
  return props.tasks.filter(task => (type.value === 'all' || task.workflow_id === type.value) &&
    [task.title, task.id, workflowLabels[task.workflow_id], task.latest_summary, task.pending_question]
      .some(text => text?.toLocaleLowerCase().includes(needle)))
})
const tabs = computed(() => groups.map(group => ({ ...group, count: candidates.value.filter(task => matches(task, group.id)).length })))
const filtered = computed(() => {
  const cutoff = Date.now() / 1000 - 3 * 24 * 60 * 60
  const rank = (task: TaskSummary) => task.status === 'completed' && task.updated_at < cutoff ? 7 : priority[task.status] ?? 9
  return candidates.value.filter(task => matches(task, status.value)).sort((left, right) =>
    rank(left) - rank(right) || right.updated_at - left.updated_at)
})
watch([search, status, type], () => { limit.value = 18 })

const cardActions: Record<string, string> = {
  waiting_human: '查看并回复', running: '查看执行', recovery_required: '检查并恢复', failed: '查看失败原因',
  completed: '查看结果', waiting_timer: '查看预约', ready: '查看排队任务', closed: '查看历史',
}
const pendingProgress: Record<string, string> = {
  running: '当前轮正在执行，结果将在结束后写入。',
  ready: '任务已进入队列，等待执行。',
  waiting_human: '有一条人工问题等待回复。',
  recovery_required: '需要核验上一轮运行。',
  closed: '任务已结束。',
}

function open(event: MouseEvent, id: string) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
  event.preventDefault()
  emit('task', id)
}
</script>

<template>
  <section class="task-lobby" aria-label="任务大厅">
    <div class="lobby-toolbar">
      <label class="field search lobby-search"><span class="sr-only">搜索任务</span><input v-model="search" type="search" placeholder="搜索任务、进展或待办…" /></label>
      <label class="field lobby-type"><span class="sr-only">工作流类型</span>
        <select v-model="type">
          <option value="all">所有工作流</option>
          <option v-for="item in types" :key="item" :value="item">{{ workflowLabels[item] ?? item }}</option>
        </select>
      </label>
    </div>
    <div class="lobby-tabs" role="group" aria-label="按任务状态筛选">
      <button v-for="tab in tabs" :key="tab.id" :aria-pressed="status === tab.id" @click="status = tab.id">
        <i v-if="tab.tone" class="status-dot" :data-status="tab.tone"></i>{{ tab.name }}
        <span class="tab-count">{{ tab.count }}</span>
      </button>
    </div>
    <div v-if="filtered.length" class="task-cards">
      <article v-for="task in filtered.slice(0, limit)" :key="task.id" class="lobby-card" :data-status="task.status">
        <div class="lobby-card-meta">
          <span class="lobby-workflow">{{ workflowLabels[task.workflow_id] ?? task.workflow_id }}</span>
          <span class="chip" :data-status="task.status"><i class="status-dot"></i>{{ label(task.status) }}</span>
        </div>
        <h2><a :href="'?task=' + encodeURIComponent(task.id)" @click="open($event, task.id)">{{ task.title }}</a></h2>
        <div class="card-progress" :class="{ 'card-question': task.pending_question }">
          <p v-if="task.pending_question" class="progress-label">等待回复</p>
          <p v-else-if="task.latest_summary" class="progress-label">{{ task.status === 'running' ? '上一轮结果' : '最新进展' }}</p>
          <p v-if="task.pending_question || task.latest_summary" class="card-excerpt">{{ task.pending_question || task.latest_summary }}</p>
          <p v-else class="card-excerpt muted">{{ pendingProgress[task.status] ?? '暂无执行记录。' }}</p>
          <p v-if="!task.workflow_enabled && task.status !== 'closed'" class="card-schedule">工作流已停用</p>
          <p v-else-if="task.next_wakeup_at" class="card-schedule">下次唤醒 <time :datetime="timestamp(task.next_wakeup_at)">{{ date(task.next_wakeup_at) }}</time></p>
        </div>
        <div class="card-bottom">
          <span class="card-activity">活跃 <time :datetime="timestamp(task.updated_at)">{{ date(task.updated_at) }}</time></span>
          <span class="card-action" aria-hidden="true">{{ task.workflow_enabled ? cardActions[task.status] ?? '查看任务' : '查看任务' }} <i></i></span>
        </div>
      </article>
    </div>
    <section v-else class="empty-board lobby-empty">
      <h2>{{ !loaded ? '正在读取任务' : tasks.length ? '没有符合条件的任务' : '还没有任务' }}</h2>
      <p v-if="loaded && !tasks.length">新建 SDD 任务后，可以在这里跟踪进度和回复人工待办。</p>
      <button v-if="loaded && tasks.length" @click="search = ''; status = 'all'; type = 'all'">清除筛选</button>
    </section>
    <div v-if="filtered.length > limit" class="show-more">
      <button @click="limit += 18">显示更多任务（已显示 {{ limit }} / {{ filtered.length }}）</button>
    </div>
  </section>
</template>
