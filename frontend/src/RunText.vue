<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ text: string }>()
const blocks = computed(() => {
  const output: { text: string; code?: string }[] = []
  const pattern = /^```([^\n`]*)\n([\s\S]*?)^```[ \t]*(?=\n|$)/gm
  let offset = 0
  for (const match of props.text.matchAll(pattern)) {
    const start = match.index ?? 0
    const preceding = props.text.slice(offset, start).trim()
    if (preceding) output.push({ text: preceding })
    output.push({ text: match[2].replace(/\n$/, ''), code: match[1].trim() })
    offset = start + match[0].length
  }
  const remaining = props.text.slice(offset).trim()
  if (remaining) output.push({ text: remaining })
  return output
})
</script>

<template>
  <div v-for="(block, index) in blocks" :key="index" class="reading-block">
    <p v-if="block.code === undefined" class="prose">{{ block.text }}</p>
    <pre v-else class="reading-code" tabindex="0" :aria-label="block.code ? block.code + ' 代码' : '代码块'"><code>{{ block.text }}</code></pre>
  </div>
</template>
