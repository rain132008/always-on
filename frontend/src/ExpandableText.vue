<script setup lang="ts">
import { computed, ref } from 'vue'

const props = withDefaults(defineProps<{ text: string; limit?: number }>(), { limit: 300 })
const expanded = ref(false)
const long = computed(() => props.text.length > props.limit)
</script>

<template>
  <p class="prose">{{ long && !expanded ? text.slice(0, limit).trimEnd() + '…' : text }}</p>
  <button v-if="long" type="button" class="link" :aria-expanded="expanded" @click="expanded = !expanded">
    {{ expanded ? '收起全文' : '展开全文' }}
  </button>
</template>
