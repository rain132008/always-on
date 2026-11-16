<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { date, runnerLabel, type Run } from './api'

const props = defineProps<{ run: Run }>()
const open = ref(false)
const log = ref<string | null>(null)
const error = ref('')
let request: AbortController | undefined
let interval: number | undefined

async function readLog() {
  if (request) return
  const controller = new AbortController()
  request = controller
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(props.run.id)}/log`, {
      cache: 'no-store', signal: controller.signal,
    })
    if (!response.ok && response.status !== 404) throw new Error('log unavailable')
    const content = response.ok ? await response.text() : ''
    if (!controller.signal.aborted) {
      log.value = content.trim() ? content : ''
      error.value = ''
    }
  } catch {
    if (!controller.signal.aborted) error.value = '无法读取运行日志。'
  } finally {
    if (request === controller) request = undefined
  }
}

function clearPolling() {
  window.clearInterval(interval)
  request?.abort()
  request = undefined
}

watch([open, () => props.run.status], ([visible, status]) => {
  clearPolling()
  if (!visible) return
  void readLog()
  if (status === 'running') interval = window.setInterval(readLog, 2400)
})
onUnmounted(clearPolling)
</script>

<template>
  <details class="disclosure run-inspector" @toggle="open = ($event.currentTarget as HTMLDetailsElement).open">
    <summary>执行日志与运行信息</summary>
    <div class="inspector-body">
      <dl class="facts inline-facts">
        <dt>Run ID</dt><dd class="mono">{{ run.id }}</dd>
        <template v-if="run.runner"><dt>Runner</dt><dd>{{ runnerLabel(run.runner) }}</dd></template>
        <dt>领取时间</dt><dd>{{ date(run.created_at) }}</dd>
        <template v-if="run.pid"><dt>进程 ID</dt><dd class="mono">{{ run.pid }}</dd></template>
        <template v-if="run.exit_code !== null"><dt>退出码</dt><dd>{{ run.exit_code }}</dd></template>
        <template v-if="run.resume_id"><dt>恢复的 Session</dt><dd class="mono">{{ run.resume_id }}</dd></template>
      </dl>
      <p v-if="error" class="muted small">{{ error }} <button class="link" @click="readLog">重新读取</button></p>
      <p v-else-if="log === null" class="muted small">正在读取日志…</p>
      <p v-else-if="!log" class="muted small">本轮尚无已记录的日志。</p>
      <pre v-else class="log" tabindex="0" aria-label="执行日志">{{ log }}</pre>
    </div>
  </details>
</template>
