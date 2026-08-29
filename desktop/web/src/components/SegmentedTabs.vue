<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

interface Tab {
  key: string;
  label: string;
  icon?: string;
  badge?: number | string;
  disabled?: boolean;
}

const props = withDefaults(
  defineProps<{
    modelValue: string;
    tabs: Tab[];
    size?: "sm" | "md";
    fullWidth?: boolean;
  }>(),
  { size: "md", fullWidth: false }
);

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();

const containerRef = ref<HTMLElement | null>(null);
const indicatorStyle = ref<Record<string, string>>({});

const containerClass = computed(() => [
  "segmented",
  `segmented--${props.size}`,
  { "segmented--full": props.fullWidth },
]);

function updateIndicator() {
  nextTick(() => {
    if (!containerRef.value) return;
    const activeBtn = containerRef.value.querySelector<HTMLElement>(".segmented-btn.active");
    if (!activeBtn) return;
    const containerRect = containerRef.value.getBoundingClientRect();
    const btnRect = activeBtn.getBoundingClientRect();
    indicatorStyle.value = {
      transform: `translateX(${btnRect.left - containerRect.left - 4}px)`,
      width: `${btnRect.width}px`,
    };
  });
}

function select(key: string, disabled?: boolean) {
  if (disabled) return;
  emit("update:modelValue", key);
}

watch(
  () => props.modelValue,
  () => {
    updateIndicator();
  },
  { flush: "post" }
);

watch(
  () => props.tabs,
  () => {
    updateIndicator();
  },
  { deep: true, flush: "post" }
);

onMounted(() => {
  updateIndicator();
  window.addEventListener("resize", updateIndicator);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", updateIndicator);
});
</script>

<template>
  <div :class="containerClass" ref="containerRef" role="tablist">
    <div class="segmented-indicator" :style="indicatorStyle" aria-hidden="true" />
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      role="tab"
      :aria-selected="modelValue === tab.key"
      :aria-disabled="tab.disabled || false"
      :disabled="tab.disabled || false"
      class="segmented-btn"
      :class="{ active: modelValue === tab.key }"
      @click="select(tab.key, tab.disabled)"
    >
      <v-icon v-if="tab.icon" :icon="tab.icon" size="16" />
      <span class="segmented-btn-label">{{ tab.label }}</span>
      <span v-if="tab.badge !== undefined && tab.badge !== 0" class="segmented-badge">
        {{ tab.badge }}
      </span>
    </button>
  </div>
</template>

<style scoped>
.segmented {
  display: inline-flex;
  position: relative;
  padding: 4px;
  gap: 2px;
  border-radius: var(--radius-pill);
  background: var(--surface-input);
  border: 1px solid var(--glass-border-2);
  box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.18), 0 4px 14px rgba(0, 0, 0, 0.12);
  -webkit-backdrop-filter: blur(var(--glass-blur-2)) saturate(1.4);
  backdrop-filter: blur(var(--glass-blur-2)) saturate(1.4);
  width: fit-content;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.segmented::-webkit-scrollbar {
  display: none;
}

.segmented--full {
  width: 100%;
  display: flex;
}

.segmented--full .segmented-btn {
  flex: 1;
  justify-content: center;
}

.segmented--full .segmented-indicator {
  /* indicator still works for full width */
}

.segmented--sm {
  padding: 3px;
}

.segmented-indicator {
  position: absolute;
  top: 4px;
  left: 0;
  height: calc(100% - 8px);
  border-radius: var(--radius-pill);
  background: var(--grad-accent);
  box-shadow: 0 6px 16px rgba(124, 92, 255, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.42);
  transition: transform var(--transition-slow), width var(--transition-slow);
  pointer-events: none;
  z-index: 0;
}

.segmented--sm .segmented-indicator {
  top: 3px;
  height: calc(100% - 6px);
}

.segmented-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.01em;
  border-radius: var(--radius-pill);
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.2s ease, background-color 0.2s ease;
  font-family: inherit;
  position: relative;
  z-index: 1;
}

.segmented--sm .segmented-btn {
  padding: 6px 12px;
  font-size: 12px;
}

.segmented-btn:hover:not(:disabled) {
  color: var(--text-primary);
}

.segmented-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.segmented-btn.active {
  color: #fff;
}

.segmented-btn.active:hover {
  filter: brightness(1.05);
}

.segmented-btn :deep(.v-icon) {
  color: inherit !important;
}

.segmented-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  border-radius: var(--radius-pill);
  background: var(--glass-bg-3);
  color: var(--text-muted);
  font-size: 10.5px;
  font-weight: 700;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  transition: background 0.2s ease, color 0.2s ease;
}

.segmented-btn.active .segmented-badge {
  background: rgba(255, 255, 255, 0.28);
  color: #fff;
}

@media (max-width: 480px) {
  .segmented {
    width: 100%;
    display: flex;
  }

  .segmented-btn {
    flex: 1;
    justify-content: center;
    padding: 8px 10px;
  }

  .segmented-btn-label {
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

@media (prefers-reduced-motion: reduce) {
  .segmented-indicator {
    transition: none !important;
  }

  .segmented-btn {
    transition: none !important;
  }
}
</style>
