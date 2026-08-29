<script setup lang="ts">
withDefaults(defineProps<{ cols?: number; gap?: number }>(), { cols: 2, gap: 16 });
</script>

<template>
  <div class="form-grid" :style="{ '--cols': cols, '--gap': `${gap}px` }">
    <slot />
  </div>
</template>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: repeat(var(--cols, 2), minmax(0, 1fr));
  gap: var(--gap, 12px);
  transition: all var(--transition);
}

@media (max-width: 768px) {
  .form-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.form-grid > :deep(.col-span-full),
.form-grid > *:first-child:last-child {
  grid-column: 1 / -1;
}
</style>
