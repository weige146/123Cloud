<script setup lang="ts">
import { computed, useSlots } from "vue";

type Accent = "group" | "success" | "warning" | "error" | "info" | "none";
type Span = 1 | 2 | "full";
type Glass = 1 | 2 | 3;

const props = withDefaults(
  defineProps<{
    title?: string;
    desc?: string;
    icon?: string;
    accent?: Accent;
    span?: Span;
    padded?: boolean;
    glass?: Glass;
    hover?: boolean;
  }>(),
  { accent: "group", span: 1, padded: true, glass: 1, hover: true, title: "", desc: "", icon: "" }
);

const slots = useSlots();

const accentAttr = computed(() => props.accent);

const spanClass = computed(() => {
  if (props.span === 2) return "span-2";
  if (props.span === "full") return "span-full";
  return "";
});

const glassClass = computed(() => `glass-${props.glass}`);

const cardClass = computed(() => [
  "glass-card",
  spanClass.value,
  glassClass.value,
  { "glass-card--no-hover": !props.hover },
]);

const showHead = computed(() => !!(props.title || props.icon || props.desc || slots.actions));
</script>

<template>
  <section :class="cardClass" :data-accent="accentAttr">
    <header v-if="showHead" class="glass-card-head">
      <div v-if="icon" class="glass-card-icon">
        <v-icon :icon="icon" />
      </div>
      <div class="glass-card-title-text">
        <h2 v-if="title" class="glass-card-title">{{ title }}</h2>
        <p v-if="desc" class="glass-card-desc">{{ desc }}</p>
      </div>
      <div v-if="$slots.actions" class="glass-card-actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="glass-card-body" :class="{ padded }">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.glass-card {
  position: relative;
  padding: 0;
  border-radius: var(--radius-surface);
  background: var(--glass-bg-2);
  border: 1px solid var(--glass-border-2);
  box-shadow: var(--surface-shadow), inset 0 1px 0 var(--glass-highlight);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
  backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
  overflow: hidden;
  transition: transform var(--transition), box-shadow var(--transition), border-color var(--transition);
}

/* Liquid sheen across the top of the panel */
.glass-card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 44%;
  background: var(--surface-sheen);
  pointer-events: none;
  z-index: 1;
}

.glass-card:not(.glass-card--no-hover):hover {
  transform: translateY(-2px);
  border-color: var(--glass-border-1);
  box-shadow: var(--shadow-lift), inset 0 1px 0 var(--glass-highlight);
}

.glass-card.glass-2 {
  background: var(--glass-bg-3);
  border-color: var(--glass-border-3);
  box-shadow: var(--shadow-sm), inset 0 1px 0 var(--glass-highlight);
}
.glass-card.glass-3 {
  background: transparent;
  border-color: var(--glass-border-3);
  -webkit-backdrop-filter: none;
  backdrop-filter: none;
  box-shadow: none;
}

.span-2 { grid-column: span 2; }
.span-full { grid-column: 1 / -1; }

.glass-card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px 0;
  position: relative;
  z-index: 2;
}

.glass-card-title-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.glass-card-icon {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  color: #fff;
  flex-shrink: 0;
  background: var(--grad-accent);
  box-shadow: 0 8px 18px rgba(124, 92, 255, 0.32), inset 0 1px 0 rgba(255, 255, 255, 0.45);
}

.glass-card[data-accent="success"] .glass-card-icon { background: var(--grad-success); box-shadow: 0 8px 18px rgba(16, 185, 129, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.45); }
.glass-card[data-accent="warning"] .glass-card-icon { background: var(--grad-warning); box-shadow: 0 8px 18px rgba(245, 158, 11, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.45); }
.glass-card[data-accent="error"] .glass-card-icon { background: var(--grad-error); box-shadow: 0 8px 18px rgba(244, 63, 94, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.45); }
.glass-card[data-accent="info"] .glass-card-icon { background: var(--grad-info); box-shadow: 0 8px 18px rgba(59, 130, 246, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.45); }
.glass-card[data-accent="none"] .glass-card-icon {
  background: var(--surface-subtle);
  color: var(--text-secondary);
  box-shadow: none;
  border: 1px solid var(--border);
}

.glass-card-icon :deep(.v-icon) {
  color: inherit !important;
  font-size: 19px !important;
}

.glass-card-title {
  font-size: 14.5px;
  font-weight: 650;
  color: var(--text-primary);
  margin: 0;
  line-height: 1.3;
  letter-spacing: -0.01em;
}

.glass-card-desc {
  color: var(--text-muted);
  font-size: 12px;
  margin: 0;
  line-height: 1.5;
}

.glass-card-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-left: auto;
}

.glass-card-body {
  padding: 0;
  position: relative;
  z-index: 2;
}

.glass-card-body.padded {
  padding: 14px 18px 18px;
}

@media (max-width: 768px) {
  .span-2, .span-full {
    grid-column: span 1;
  }

  .glass-card-head {
    padding: 16px 16px 12px;
    flex-wrap: wrap;
  }

  .glass-card-actions {
    width: 100%;
  }

  .glass-card-body.padded {
    padding: 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .glass-card:not(.glass-card--no-hover):hover {
    transform: none !important;
  }
}
</style>
