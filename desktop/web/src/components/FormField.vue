<script setup lang="ts">
withDefaults(defineProps<{ label?: string; hint?: string; full?: boolean }>(), { full: false });
</script>

<template>
  <div :class="{ 'col-span-full': full }">
    <label v-if="label" class="form-field-label">{{ label }}</label>
    <slot />
    <p v-if="hint" class="form-field-hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.form-field-label {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 3px;
  display: block;
  letter-spacing: 0.01em;
  transition: color var(--transition);
}

.form-field-hint {
  font-size: 11.5px;
  color: var(--text-muted);
  margin-top: 3px;
  line-height: 1.45;
  transition: color var(--transition);
}
</style>
