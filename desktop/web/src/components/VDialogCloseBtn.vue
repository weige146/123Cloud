<script lang="ts" setup>
// 通用对话框关闭按钮。
// 默认绝对定位在对话框右上角；可通过 inner-class 覆盖位置
const props = defineProps({
  innerClass: { type: String, default: '' },
})

const emit = defineEmits<{
  (e: 'click'): void
  (e: 'update:modelValue', value: boolean): void
}>()

function onClick() {
  emit('update:modelValue', false)
  emit('click')
}
</script>

<template>
  <VBtn
    icon="mdi-close"
    size="small"
    variant="text"
    :class="[props.innerClass || 'absolute right-3 top-3 z-10', 'v-dialog-close-btn']"
    @click.stop="onClick"
  />
</template>

<style scoped>
.v-dialog-close-btn {
  transition: color var(--transition), background-color var(--transition) !important;
}

.v-dialog-close-btn:hover {
  color: var(--accent) !important;
  background: var(--bg-hover) !important;
}

.v-dialog-close-btn:focus-visible {
  color: var(--accent) !important;
  box-shadow: 0 0 0 2px var(--accent-soft) !important;
}

html.dark .v-dialog-close-btn:hover {
  background: var(--accent-soft) !important;
}
</style>
