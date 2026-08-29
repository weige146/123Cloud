<script setup lang="ts">
import { computed } from "vue";

type GroupKey = "dashboard" | "pan115" | "recognition" | "media";

const props = withDefaults(
  defineProps<{
    title: string;
    desc?: string;
    icon: string;
    group?: GroupKey;
  }>(),
  { group: "dashboard", desc: "" }
);

const wrapperClass = computed(() => `page-hero page-hero--${props.group}`);
</script>

<template>
  <header :class="wrapperClass" :data-group="group">
    <div class="page-hero-icon">
      <v-icon :icon="icon" />
    </div>
    <div class="page-hero-text">
      <h1 class="page-hero-title">{{ title }}</h1>
      <p v-if="desc" class="page-hero-desc">{{ desc }}</p>
      <div v-if="$slots.status" class="page-hero-status">
        <slot name="status" />
      </div>
      <div v-if="$slots.meta" class="page-hero-meta">
        <slot name="meta" />
      </div>
    </div>
    <div v-if="$slots.actions" class="page-hero-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-hero {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px 22px;
  border-radius: var(--radius-dialog);
  border: 1px solid var(--glass-border-1);
  background: linear-gradient(135deg, rgba(124, 92, 255, 0.15), rgba(76, 201, 240, 0.06) 55%, transparent),
    var(--glass-bg-1);
  -webkit-backdrop-filter: blur(var(--glass-blur-heavy)) saturate(var(--glass-saturate));
  backdrop-filter: blur(var(--glass-blur-heavy)) saturate(var(--glass-saturate));
  box-shadow: var(--surface-shadow), inset 0 1px 0 var(--glass-highlight);
  overflow: hidden;
  position: relative;
  flex-wrap: wrap;
  min-width: 0;
}

/* soft aurora halo in the corner */
.page-hero::after {
  content: "";
  position: absolute;
  right: -70px;
  top: -90px;
  width: 260px;
  height: 260px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(124, 92, 255, 0.3), transparent 65%);
  filter: blur(28px);
  pointer-events: none;
}
.page-hero--pan115::after { background: radial-gradient(circle, rgba(76, 201, 240, 0.28), transparent 65%); }
.page-hero--recognition::after { background: radial-gradient(circle, rgba(251, 191, 36, 0.22), transparent 65%); }
.page-hero--media::after { background: radial-gradient(circle, rgba(244, 114, 182, 0.24), transparent 65%); }

.page-hero-icon {
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  display: grid;
  place-items: center;
  border-radius: 15px;
  color: #fff;
  background: var(--grad-accent);
  box-shadow: 0 10px 26px rgba(124, 92, 255, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.45);
}

.page-hero--pan115 .page-hero-icon { background: linear-gradient(135deg, #0ea5e9, #4cc9f0); box-shadow: 0 10px 26px rgba(14, 165, 233, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.45); }
.page-hero--recognition .page-hero-icon { background: linear-gradient(135deg, #f59e0b, #fbbf24); box-shadow: 0 10px 26px rgba(245, 158, 11, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.45); }
.page-hero--media .page-hero-icon { background: linear-gradient(135deg, #ec4899, #f472b6); box-shadow: 0 10px 26px rgba(236, 72, 153, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.45); }

.page-hero-icon :deep(.v-icon) { font-size: 23px !important; }

.page-hero-text {
  flex: 1 1 340px;
  min-width: 0;
  position: relative;
}

.page-hero-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 720;
  line-height: 1.25;
  letter-spacing: -0.02em;
}

.page-hero-desc {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 12.5px;
  line-height: 1.55;
}

.page-hero-title,
.page-hero-desc {
  max-width: 100%;
  overflow-wrap: anywhere;
}

.page-hero-status,
.page-hero-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  position: relative;
}

.page-hero-actions {
  margin-left: auto;
  justify-content: flex-end;
}

.page-hero-actions :deep(.v-btn) {
  min-height: 38px;
}

.page-hero-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-muted);
}

@media (max-width: 768px) {
  .page-hero {
    gap: 12px;
    padding: 16px 18px;
  }
}

@media (max-width: 480px) {
  .page-hero {
    flex-direction: column;
    gap: 10px;
  }

  .page-hero-actions {
    width: 100%;
    margin-left: 0;
  }

  .page-hero-text {
    flex: 0 1 auto;
    width: 100%;
  }

  .page-hero-actions :deep(.v-btn) {
    flex: 1 1 140px;
  }

  .page-hero-title { font-size: 18px; }
}
</style>
