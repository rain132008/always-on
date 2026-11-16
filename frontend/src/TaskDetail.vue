<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { date, eventLabels, label, resumeRestriction, timestamp, workflowLabels, type Run, type Task } from './api'
import RunDiagnostics from './RunDiagnostics.vue'
import RunText from './RunText.vue'

const props = defineProps<{ task: Task; busy: boolean; online: boolean; feedback: string; operationError: string }>()
const emit = defineEmits<{
  back: []
  close: [task: Task]
  reply: [request: string, answer: string]
  resume: [session: string, instruction: string]
  recover: [run: string, operation: 'stop' | 'retry']
  session: [id: string]
  workflow: [id: string]
}>()

const draft = ref('')
const composerInput = ref<HTMLTextAreaElement | null>(null)
const request = computed(() => props.task.status === 'waiting_human'
  ? props.task.requests.find(item => item.answer === null) : undefined)
const latestRun = computed(() => props.task.runs.at(-1))
const recovery = computed(() => [...props.task.runs].reverse().find(run => run.status === 'recovery_required'))
const canResume = computed(() => !resumeRestriction(props.task.status, props.task.workflow_enabled, props.task.session.external_id))
const composer = computed(() => Boolean(request.value && props.task.workflow_enabled) || canResume.value)
const origin = computed(() => {
  const source = props.task.events[0]?.kind
  if (source === 'task_created') return '手动创建'
  return source ? eventLabels[source] ?? '工作流触发' : '工作流触发'
})
const waiting = computed(() => {
  const task = props.task
  if (task.status === 'closed') return '任务已关闭，后续唤醒已取消。'
  if (!task.workflow_enabled) return '工作流已停用。重新启用后继续处理。'
  if (request.value) return '收到回复后，Agent 会在原会话继续。'
  if (task.next_wakeup) return '到期后自动继续原会话。'
  if (task.status === 'running') return '本轮结束后，执行结果会出现在记录中。'
  if (recovery.value) return '核验上一轮运行后，可重新排队。'
  if (task.status === 'ready') return '等待执行器领取。'
  if (task.status === 'failed') return '可查看失败原因与执行日志。'
  if (task.status === 'completed') return canResume.value ? '可以补充指令，继续原会话。' : '任务已完成。'
  return ''
})

type Artifact = Task['artifacts'][number]
type EntryBase = { key: string; at: number; order: number }
type Entry = EntryBase & (
  | { kind: 'user' | 'system' | 'question'; title: string; text?: string; current?: boolean; sourceDetails?: string }
  | { kind: 'run'; run: Run; artifacts: Artifact[] }
)

const entries = computed(() => {
  const task = props.task
  const artifactsByRun = new Map<string, Artifact[]>()
  for (const artifact of task.artifacts) {
    const artifacts = artifactsByRun.get(artifact.run_id) ?? []
    artifacts.push(artifact)
    artifactsByRun.set(artifact.run_id, artifacts)
  }
  const scheduledSummaries = new Map(task.runs.filter(run => run.status === 'schedule_wakeup').map(run => [run.finished_at, run.summary]))
  const list: Entry[] = []
  for (const event of task.events) {
    const author: Record<string, string> = { task_created: '你 · 初始指令', human_replied: '你 · 人工回复', manual_resume: '你 · 补充指令' }
    const title = author[event.kind]
    const fromSource = event.kind === 'mr_head' || event.kind === 'merged_commit'
    const excerpt = fromSource ? event.summary.replace(/\b[\da-f]{32,64}\b/gi, sha => `${sha.slice(0, 8)}…`) : event.summary
    list.push({ key: 'event-' + event.id, at: event.created_at, order: event.kind === 'agent_wakeup' ? 2 : 0,
      kind: title ? 'user' : 'system', title: title ?? eventLabels[event.kind] ?? '工作流事件',
      text: event.kind === 'agent_wakeup' && scheduledSummaries.get(event.created_at) === event.summary ? undefined : excerpt,
      sourceDetails: excerpt === event.summary ? undefined : event.summary })
  }
  for (const run of task.runs) {
    list.push({ key: 'run-' + run.id, at: run.finished_at ?? run.created_at, order: 1,
      kind: 'run', run, artifacts: artifactsByRun.get(run.id) ?? [] })
  }
  for (const question of task.requests) {
    list.push({ key: 'question-' + question.id, at: question.created_at, order: 3,
      kind: 'question', title: 'Agent 请求澄清', text: question.question, current: question.id === request.value?.id })
  }
  return list.sort((left, right) => left.at - right.at || left.order - right.order)
})

function jump(id: string) {
  const target = document.getElementById(id)
  target?.scrollIntoView({ block: 'nearest' })
  target?.focus({ preventScroll: true })
}

function focusComposer() {
  composerInput.value?.scrollIntoView({ block: 'center' })
  composerInput.value?.focus({ preventScroll: true })
}

function send() {
  const text = draft.value.trim()
  if (!text || props.busy || !props.online) return
  if (request.value && props.task.workflow_enabled) emit('reply', request.value.id, text)
  else if (canResume.value) emit('resume', props.task.session.id, text)
}

watch(() => props.feedback, feedback => { if (feedback) draft.value = '' })
</script>

<template>
  <section class="task-space" aria-label="任务详情">
    <div class="task-context-bar">
      <button class="back-link" @click="emit('back')"><i></i>任务大厅</button>
      <span class="context-divider" aria-hidden="true">/</span>
      <button class="context-workflow" @click="emit('workflow', task.workflow_id)">{{ workflowLabels[task.workflow_id] ?? task.workflow_id }}</button>
    </div>
    <header class="space-heading">
      <div>
        <h1 id="task-heading" tabindex="-1">{{ task.title }}</h1>
        <div class="space-meta">
          <span class="chip" :data-status="task.status" role="status"><i class="status-dot"></i>{{ label(task.status) }}</span>
          <span>最近活跃 <time :datetime="timestamp(task.updated_at)">{{ date(task.updated_at) }}</time></span>
        </div>
        <p v-if="task.next_wakeup && task.status === 'waiting_timer'" class="mobile-wakeup">
          {{ task.workflow_enabled ? '下次自动唤醒' : '已暂停的唤醒' }} <time :datetime="timestamp(task.next_wakeup.due_at)">{{ date(task.next_wakeup.due_at) }}</time>
        </p>
      </div>
      <div class="space-actions">
        <button v-if="composer" class="primary" @click="focusComposer">{{ request ? '回复待办' : '补充指令' }}</button>
        <button v-if="task.status !== 'closed'" class="quiet-button" :disabled="busy || !online" @click="emit('close', task)">关闭任务</button>
      </div>
    </header>
    <div class="space-layout">
      <section class="workstream" aria-label="对话与执行">
        <header class="stream-header">
          <h2>对话与执行</h2>
          <button v-if="entries.length > 2" class="stream-jump" @click="jump('activity-end')">跳至最新 <span aria-hidden="true">↓</span></button>
        </header>
        <ol class="stream">
          <li v-if="!entries.length" class="stream-entry stream-user">
            <span class="entry-icon" aria-hidden="true">你</span>
            <div class="entry-main"><div class="entry-meta"><strong>任务说明</strong></div><div class="message message-user"><RunText :text="task.prompt" /></div></div>
          </li>
          <li v-for="item in entries" :id="item.key" :key="item.key" :class="['stream-entry', 'stream-' + item.kind]" tabindex="-1">
            <span class="entry-icon" aria-hidden="true">{{ item.kind === 'user' ? '你' : item.kind === 'run' ? 'A' : item.kind === 'question' ? '?' : '' }}</span>
            <div class="entry-main">
              <div class="entry-meta">
                <template v-if="item.kind === 'run'">
                  <strong>{{ item.run.agent_name ?? 'Agent' }}</strong>
                  <span class="entry-round">第 {{ item.run.generation }} 轮<span v-if="item.run.resume_id"> · 续跑原会话</span></span>
                  <span class="chip" :data-status="item.run.status">{{ label(item.run.status) }}</span>
                </template>
                <template v-else>
                  <strong>{{ item.title }}</strong>
                  <span v-if="item.current" class="chip" data-status="waiting_human">等待回复</span>
                </template>
                <time :datetime="timestamp(item.at)">{{ date(item.at) }}</time>
              </div>
              <template v-if="item.kind === 'run'">
                <div class="message message-agent">
                  <RunText v-if="item.run.summary" :text="item.run.summary" />
                  <p v-else-if="item.run.status === 'running'" class="running-message"><i class="status-dot" data-status="running"></i>正在执行本轮任务。</p>
                  <template v-for="artifact in item.artifacts" :key="artifact.id">
                    <section :id="'artifact-' + artifact.id" class="result-artifact" tabindex="-1">
                      <template v-if="artifact.kind === 'document_suggestion'">
                        <h3>{{ artifact.content.document }}</h3>
                        <p class="artifact-label">文档更新建议</p>
                        <RunText :text="artifact.content.suggestion" />
                        <details class="disclosure"><summary>关联提交</summary><p class="mono">{{ artifact.content.commit_sha }}</p></details>
                      </template>
                      <template v-else>
                        <h3>MR 评审报告</h3>
                        <RunText v-if="item.run.summary !== artifact.content.summary" :text="artifact.content.summary" />
                        <ol v-if="artifact.content.findings.length" class="review-findings">
                          <li v-for="(finding, index) in artifact.content.findings" :key="index"><strong class="mono">{{ finding.path }}</strong><RunText :text="finding.detail" /></li>
                        </ol>
                        <p v-else class="muted">本次评审未记录发现项。</p>
                        <details class="disclosure"><summary>评审版本</summary><p class="mono">{{ artifact.content.head_sha }}</p></details>
                      </template>
                    </section>
                  </template>
                </div>
                <RunDiagnostics :run="item.run" />
              </template>
              <div v-else-if="item.text" :class="['message', 'message-' + item.kind]">
                <RunText :text="item.text" />
                <details v-if="item.sourceDetails" class="disclosure source-details"><summary>完整触发信息</summary><RunText :text="item.sourceDetails" /></details>
              </div>
            </div>
          </li>
        </ol>
        <div id="activity-end" class="activity-end" tabindex="-1">
          <form v-if="composer" class="task-composer" @submit.prevent="send">
            <label class="field">
              <span>{{ request ? '回复人工问题' : '补充指令' }}</span>
              <textarea ref="composerInput" v-model="draft" rows="3" maxlength="12000" required :disabled="busy"
                :placeholder="request ? '输入确认意见或补充背景…' : '告诉 Agent 接下来要做什么…'" />
            </label>
            <div class="composer-footer">
              <span class="muted">使用原会话</span>
              <button class="primary" :disabled="busy || !online || !draft.trim()">{{ request ? '回复并恢复会话' : '继续原会话' }}</button>
            </div>
          </form>
          <p v-if="composer && operationError" class="error stream-feedback" role="alert">{{ operationError }}</p>
          <p v-if="feedback && task.status === 'ready'" class="stream-feedback" role="status">{{ feedback }}</p>
        </div>
      </section>
      <aside class="space-rail" aria-label="任务状态与上下文">
        <section class="rail-section rail-next" :data-status="task.status">
          <h2>下一步</h2>
          <time v-if="task.next_wakeup && task.status !== 'closed'" class="rail-due" :datetime="timestamp(task.next_wakeup.due_at)">{{ date(task.next_wakeup.due_at) }}</time>
          <p>{{ waiting || label(task.status) }}</p>
          <button v-if="request" class="rail-link" @click="jump('question-' + request.id)">定位到澄清问题 <span aria-hidden="true">↗</span></button>
          <button v-else-if="latestRun && ['running', 'failed', 'recovery_required'].includes(task.status)" class="rail-link" @click="jump('run-' + latestRun.id)">查看本轮执行 <span aria-hidden="true">↗</span></button>
          <div v-if="recovery" class="rail-recovery">
            <button v-if="recovery.pid || task.status === 'closed'" :disabled="busy || !online" @click="emit('recover', recovery.id, 'stop')">{{ recovery.pid ? '停止旧进程' : '确认旧运行结束' }}</button>
            <button v-if="task.status !== 'closed' && task.workflow_enabled" class="primary" :disabled="busy || !online" @click="emit('recover', recovery.id, 'retry')">核验并重新排队</button>
          </div>
          <button v-if="!task.workflow_enabled && task.status !== 'closed'" class="rail-link" @click="emit('workflow', task.workflow_id)">查看关联工作流</button>
          <p v-if="!composer && operationError" class="error stream-feedback" role="alert">{{ operationError }}</p>
        </section>
        <section class="rail-section">
          <h2>会话与来源</h2>
          <dl class="rail-facts">
            <dt>触发来源</dt><dd>{{ origin }}</dd>
            <dt>会话</dt><dd>{{ task.session.external_id ? '已持久化' : '等待首次登记' }}</dd>
            <template v-if="latestRun?.agent_name"><dt>执行 Agent</dt><dd>{{ latestRun.agent_name }}</dd></template>
          </dl>
          <button class="rail-link" @click="emit('session', task.session.id)">在会话中心打开 <span aria-hidden="true">↗</span></button>
          <details class="disclosure rail-technical">
            <summary>标识与运行环境</summary>
            <dl class="facts disclosure-content">
              <dt>任务 ID</dt><dd class="mono">{{ task.id }}</dd>
              <dt>Session ID</dt><dd class="mono">{{ task.session.external_id || '待登记' }}</dd>
              <dt>工作区</dt><dd class="mono">{{ task.session.workspace }}</dd>
            </dl>
          </details>
        </section>
        <section v-if="task.artifacts.length" class="rail-section">
          <h2>工作流产出 <span class="muted small">{{ task.artifacts.length }}</span></h2>
          <ul class="rail-artifacts">
            <li v-for="artifact in [...task.artifacts].reverse()" :key="artifact.id">
              <button class="rail-link" @click="jump('artifact-' + artifact.id)">{{ artifact.kind === 'mr_review' ? 'MR 评审报告' : artifact.content.document }}</button>
              <time :datetime="timestamp(artifact.created_at)">{{ date(artifact.created_at) }}</time>
            </li>
          </ul>
        </section>
      </aside>
    </div>
  </section>
</template>
