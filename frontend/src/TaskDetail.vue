<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { date, eventLabels, label, resumeRestriction, runnerLabel, workflowLabels, type Run, type Task } from './api'
import ExpandableText from './ExpandableText.vue'

const props = defineProps<{ task: Task; busy: boolean; online: boolean }>()
const emit = defineEmits<{
  close: [task: Task]
  reply: [request: string, answer: string]
  recover: [run: string, operation: 'stop' | 'retry']
  session: [id: string]
  workflow: [id: string]
}>()

const answer = ref('')
const shownLog = ref<string | null>(null)
const log = ref('')
let logVersion = 0

const request = computed(() => props.task.status === 'waiting_human'
  ? props.task.requests.find(item => item.answer === null) : undefined)
watch(() => request.value?.id, () => { answer.value = '' })

const recentRuns = computed(() => [...props.task.runs].reverse())
const latest = computed(() => recentRuns.value.find(run => run.summary))
const recovery = computed(() => recentRuns.value.find(run => run.status === 'recovery_required'))
const canResume = computed(() => !resumeRestriction(
  props.task.status, props.task.workflow_enabled, props.task.session.external_id,
))

type Activity = {
  key: string; at: number; order: number; title: string; text?: string | null; status?: string; run?: Run
}

const activity = computed(() => {
  const task = props.task
  const runSummaries = new Map(task.runs.map(run => [run.finished_at, run.summary]))
  const items: Activity[] = task.events.map(event => ({
    key: 'event-' + event.id,
    at: event.created_at,
    order: 3,
    title: eventLabels[event.kind] ?? event.kind,
    text: event.summary === task.prompt || runSummaries.get(event.created_at) === event.summary ? null : event.summary,
    status: event.kind === 'task_closed' ? 'closed' : undefined,
  }))
  for (const run of task.runs) {
    items.push({
      key: 'run-' + run.id,
      at: run.finished_at ?? run.created_at,
      order: 1,
      title: '第 ' + run.generation + ' 轮' + (run.resume_id ? ' · 恢复会话' : ''),
      // 最新结论已在详情上方展示，运行记录仍保留状态和日志入口。
      text: latest.value?.id === run.id ? null : run.summary,
      status: run.status,
      run,
    })
  }
  for (const clarification of task.requests) {
    items.push({
      key: 'request-' + clarification.id,
      at: clarification.created_at,
      order: 2,
      title: '提出人工问题',
      text: clarification.id === request.value?.id ? null : clarification.question,
      status: clarification.id === request.value?.id ? 'waiting_human' : undefined,
    })
  }
  return items.sort((left, right) => right.at - left.at || right.order - left.order)
})

const nextDescription = computed(() => {
  const task = props.task
  if (task.status === 'closed') return '后续唤醒已取消。'
  if (!task.workflow_enabled) return '关联工作流已停用。'
  if (task.status === 'running') return '当前轮正在执行。'
  if (task.status === 'ready') return '等待领取任务。'
  if (canResume.value) return '可提交补充指令，使用原会话继续。'
  if (task.status === 'failed') return '可在活动记录中查看失败原因与日志。'
  return null
})

async function toggleLog(runId: string) {
  const token = ++logVersion
  if (shownLog.value === runId) {
    shownLog.value = null
    return
  }
  shownLog.value = runId
  log.value = '正在读取运行日志…'
  try {
    const response = await fetch('/api/runs/' + runId + '/log', { cache: 'no-store' })
    const content = response.ok ? await response.text() : ''
    if (token === logVersion) log.value = content.trim() ? content : '这次运行还没有可显示的日志。'
  } catch {
    if (token === logVersion) log.value = '无法读取日志，请稍后重试。'
  }
}
</script>

<template>
  <section class="task-detail" aria-label="任务详情">
    <div class="panel task-summary">
      <header class="task-heading">
        <div>
          <div class="task-meta"><span>{{ workflowLabels[task.workflow_id] ?? task.workflow_id }}</span><time :datetime="new Date(task.updated_at * 1000).toISOString()">{{ date(task.updated_at) }}</time></div>
          <h2>{{ task.title }}</h2>
        </div>
        <div class="button-row">
          <span class="chip" :data-status="task.status" aria-live="polite">{{ label(task.status) }}</span>
          <button v-if="task.status !== 'closed'" class="quiet-button" :disabled="busy || !online" @click="emit('close', task)">关闭任务</button>
        </div>
      </header>

      <div class="task-summary-body">
        <div class="task-brief">
          <section>
            <h3>任务目标</h3>
            <ExpandableText :text="task.prompt" :limit="460" />
          </section>
          <section v-if="latest" class="latest-run">
            <div class="section-heading">
              <h3>最新执行结论</h3>
              <span class="muted small">{{ [latest.agent_name, latest.runner && runnerLabel(latest.runner), `第 ${latest.generation} 轮`].filter(Boolean).join(' · ') }}</span>
            </div>
            <ExpandableText v-if="latest.summary" :key="latest.id" :text="latest.summary" />
          </section>
        </div>

        <section class="next-step" aria-label="下一步">
          <h3>下一步</h3>
          <div v-if="request" class="next-step-content" data-step="human">
            <h4>人工待办</h4>
            <p class="prose">{{ request.question }}</p>
            <form v-if="task.workflow_enabled" class="management-form" @submit.prevent="emit('reply', request.id, answer)">
              <label class="field">确认回复<textarea v-model="answer" rows="3" required maxlength="12000" placeholder="填写确认意见" /></label>
              <button class="primary" :disabled="busy || !online || !answer.trim()">回复并恢复会话</button>
            </form>
          </div>
          <div v-else-if="task.next_wakeup" class="next-step-content">
            <h4>下次唤醒</h4>
            <time class="wakeup-time" :datetime="new Date(task.next_wakeup.due_at * 1000).toISOString()">{{ date(task.next_wakeup.due_at) }}</time>
            <p v-if="task.workflow_enabled" class="muted">到点后自动继续原会话。</p>
          </div>
          <div v-else-if="recovery" class="next-step-content">
            <h4>检查运行</h4>
            <ExpandableText v-if="recovery.summary && recovery.id !== latest?.id" :text="recovery.summary" />
            <div class="button-row">
              <button v-if="recovery.pid || task.status === 'closed'" :disabled="busy || !online" @click="emit('recover', recovery.id, 'stop')">{{ recovery.pid ? '停止旧进程' : '确认旧运行结束' }}</button>
              <button v-if="task.status !== 'closed' && task.workflow_enabled" class="primary" :disabled="busy || !online" @click="emit('recover', recovery.id, 'retry')">核验并重新排队</button>
            </div>
          </div>
          <div v-if="nextDescription && (!request && !task.next_wakeup && !recovery || !task.workflow_enabled)" class="next-step-content">
            <p class="muted">{{ nextDescription }}</p>
            <button v-if="canResume" class="primary" @click="emit('session', task.session.id)">继续原会话</button>
          </div>
          <button v-if="!task.workflow_enabled && task.status !== 'closed'" class="link" @click="emit('workflow', task.workflow_id)">查看关联工作流</button>
        </section>
      </div>
    </div>

    <section class="panel activity-panel" aria-label="活动时间线">
      <div class="section-heading"><h3>活动时间线</h3><span class="muted small">{{ activity.length }} 条记录</span></div>
      <p v-if="!activity.length" class="muted">暂无任务活动。</p>
      <ol v-else class="activity">
        <li v-for="item in activity" :key="item.key" class="activity-item" :data-status="item.status">
          <div class="activity-heading">
            <strong>{{ item.title }}</strong>
            <span v-if="item.status && item.status !== 'closed'" class="chip" :data-status="item.status">{{ label(item.status) }}</span>
            <time :datetime="new Date(item.at * 1000).toISOString()">{{ date(item.at) }}</time>
          </div>
          <ExpandableText v-if="item.text" :text="item.text" />
          <details v-if="item.run" class="run-technical disclosure">
            <summary>运行详情</summary>
            <dl class="facts inline-facts">
              <dt>Run ID</dt><dd class="mono">{{ item.run.id }}</dd>
              <template v-if="item.run.agent_name"><dt>Agent</dt><dd>{{ item.run.agent_name }}</dd></template>
              <template v-if="item.run.runner"><dt>Runner</dt><dd>{{ runnerLabel(item.run.runner) }}</dd></template>
              <dt>领取时间</dt><dd>{{ date(item.run.created_at) }}</dd>
              <template v-if="item.run.exit_code !== null"><dt>退出码</dt><dd>{{ item.run.exit_code }}</dd></template>
              <template v-if="item.run.resume_id"><dt>恢复的 Session</dt><dd class="mono">{{ item.run.resume_id }}</dd></template>
            </dl>
            <button class="link" :aria-expanded="shownLog === item.run.id" @click="toggleLog(item.run.id)">{{ shownLog === item.run.id ? '收起日志' : '查看日志' }}</button>
            <pre v-if="shownLog === item.run.id" class="log">{{ log }}</pre>
          </details>
        </li>
      </ol>
    </section>

    <details v-if="task.artifacts.length" class="panel disclosure">
      <summary>工作流产出 <span class="muted small">{{ task.artifacts.length }}</span></summary>
      <ol class="run-list disclosure-content">
        <li v-for="artifact in [...task.artifacts].reverse()" :key="artifact.id">
          <template v-if="artifact.kind === 'document_suggestion'">
            <div class="run-heading"><strong>{{ artifact.content.document }}</strong><time>{{ date(artifact.created_at) }}</time></div>
            <p class="mono muted">提交 {{ artifact.content.commit_sha }}</p>
            <ExpandableText :text="artifact.content.suggestion" />
          </template>
          <template v-else>
            <div class="run-heading"><strong>MR 评审报告</strong><time>{{ date(artifact.created_at) }}</time></div>
            <p class="mono muted">Head {{ artifact.content.head_sha }}</p>
            <ExpandableText :text="artifact.content.summary" />
            <ul v-if="artifact.content.findings.length">
              <li v-for="(finding, index) in artifact.content.findings" :key="index"><span class="mono">{{ finding.path }}</span> {{ finding.detail }}</li>
            </ul>
          </template>
        </li>
      </ol>
    </details>

    <details class="panel disclosure">
      <summary>会话与运行环境</summary>
      <dl class="facts inline-facts disclosure-content">
        <dt>任务 ID</dt><dd class="mono">{{ task.id }}</dd>
        <dt>Session ID</dt><dd class="mono">{{ task.session.external_id || '待登记' }}</dd>
        <dt>工作区</dt><dd class="mono">{{ task.session.workspace }}</dd>
      </dl>
      <button class="link" @click="emit('session', task.session.id)">在会话中心查看 · {{ task.session.name }}</button>
    </details>
  </section>
</template>
