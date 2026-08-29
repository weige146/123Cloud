<script setup lang="ts">
import { computed } from "vue";

type Tone = "group" | "success" | "warning" | "error" | "info";

interface Trend {
  dir: "up" | "down";
  text: string;
}

const props = withDefaults(
  defineProps<{
    label: string;
    value: string | number;
    icon: string;
    tone?: Tone;
    hint?: string;
    trend?: Trend;
  }>(),
  { tone: "group", hint: "" }
);

const toneAttr = computed(() => (props.tone === "group" ? false : props.tone));
const trendIcon = computed(() => (props.trend?.dir === "down" ? "mdi-trending-down" : "mdi-trending-up"));
</script>

<template>
  <div class="stat-tile" :data-tone="toneAttr">
    <div class="stat-tile-icon">
      <v-icon :icon="icon" />
    </div>
    <div class="stat-tile-body">
      <div class="stat-tile-value mono-value">{{ value }}</div>
      <div class="stat-tile-label">{{ label }}</div>
      <div v-if="hint" class="stat-tile-hint">{{ hint }}</div>
    </div>
    <div v-if="trend" class="stat-tile-trend">
      <v-icon :icon="trendIcon" size="12" />
      <span>{{ trend.text }}</span>
    </div>
  </div>
</template>

<style scoped>
.stat-tile {
  position: relative;
  min-height: 78px;
  padding: 15px 16px;
  border-radius: var(--radius-surface);
  border: 1px solid var(--glass-border-2);
  background: var(--glass-bg-2);
  -webkit-backdrop-filter: blur(var(--glass-blur-2)) saturate(var(--glass-saturate));
  backdrop-filter: blur(var(--glass-blur-2)) saturate(var(--glass-saturate));
  box-shadow: var(--shadow-sm), inset 0 1px 0 var(--glass-highlight);
  display: flex;
  align-items: center;
  gap: 13px;
  overflow: hidden;
  transition: transform var(--transition), border-color var(--transition), box-shadow var(--transition);
}

.stat-tile:hover {
  transform: translateY(-2px);
  border-color: var(--glass-border-1);
  box-shadow: var(--shadow-lift), inset 0 1px 0 var(--glass-highlight);
}

.stat-tile-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  color: #fff;
  flex-shrink: 0;
  background: var(--grad-accent);
  box-shadow: 0 8px 18px rgba(124, 92, 255, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.4);
}

.stat-tile-icon :deep(.v-icon) {
  color: inherit !important;
  font-size: 20px !important;
}

.stat-tile-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-tile-value {
  font-size: 19px !important;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  line-height: 1.2;
  background: none !important;
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
  border: none !important;
  padding: 0 !important;
}

.stat-tile-label {
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-muted);
}

.stat-tile-hint {
  font-size: 11.5px;
  color: var(--text-muted);
  margin-top: 1px;
}

.stat-tile-trend {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: var(--radius-pill);
  background: var(--group-soft);
  color: var(--group-color);
  align-self: flex-start;
  margin-top: 4px;
}

/* Tone overrides */
.stat-tile[data-tone="success"] .stat-tile-icon { background: var(--grad-success); box-shadow: 0 8px 18px rgba(16, 185, 129, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.4); }
.stat-tile[data-tone="success"] .stat-tile-value { color: var(--success); }
.stat-tile[data-tone="success"] .stat-tile-trend { background: var(--success-soft); color: var(--success); }

.stat-tile[data-tone="warning"] .stat-tile-icon { background: var(--grad-warning); box-shadow: 0 8px 18px rgba(245, 158, 11, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.4); }
.stat-tile[data-tone="warning"] .stat-tile-value { color: var(--warning); }
.stat-tile[data-tone="warning"] .stat-tile-trend { background: var(--warning-soft); color: var(--warning); }

.stat-tile[data-tone="error"] .stat-tile-icon { background: var(--grad-error); box-shadow: 0 8px 18px rgba(244, 63, 94, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.4); }
.stat-tile[data-tone="error"] .stat-tile-value { color: var(--error); }
.stat-tile[data-tone="error"] .stat-tile-trend { background: var(--error-soft); color: var(--error); }

.stat-tile[data-tone="info"] .stat-tile-icon { background: var(--grad-info); box-shadow: 0 8px 18px rgba(59, 130, 246, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.4); }
.stat-tile[data-tone="info"] .stat-tile-value { color: var(--info); }
.stat-tile[data-tone="info"] .stat-tile-trend { background: var(--info-soft); color: var(--info); }

@media (max-width: 480px) {
  .stat-tile {
    padding: 15px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stat-tile:hover {
    transform: none !important;
  }
}
</style>
