<script setup lang="ts">
defineProps<{ reply?: boolean; busy: boolean; online: boolean; problem: string }>()
const text = defineModel<string>({ required: true })
const emit = defineEmits<{ send: [] }>()
</script>

<template>
  <form class="task-composer" @submit.prevent="emit('send')">
    <label class="field" for="task-composer-input">
      <span>{{ reply ? '回复人工问题' : '补充指令' }}</span>
      <textarea id="task-composer-input" v-model="text" :rows="reply ? 2 : 3" maxlength="12000" required :disabled="busy"
        :placeholder="reply ? '写下答复或补充背景…' : '告诉 Agent 接下来要做什么…'" />
    </label>
    <p v-if="problem" class="error stream-feedback" role="alert">{{ problem }}</p>
    <div class="composer-footer">
      <span class="muted">续跑原会话</span>
      <button class="primary" :disabled="busy || !online || !text.trim()">{{ reply ? '回复并恢复会话' : '继续原会话' }}</button>
    </div>
  </form>
</template>
