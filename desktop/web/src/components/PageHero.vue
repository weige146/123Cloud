<script setup lang="ts">
import { computed } from "vue";

type GroupKey = "dashboard" | "submission" | "pan115" | "recognition" | "media";

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
/* 桌面客户端页头：一行式（图标 + 标题 + 说明 + 状态 + 操作），无横幅卡片 */
.page-hero {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 2px 10px;
  flex-wrap: wrap;
  min-width: 0;
}

.page-hero-icon {
  width: 26px;
  height: 26px;
  flex: 0 0 26px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  color: var(--group-color, var(--accent));
  background: var(--group-soft, var(--accent-soft));
  border: 1px solid var(--group-border, var(--glass-border-2));
}

.page-hero--pan115 .page-hero-icon { color: #4cc9f0; background: rgba(76, 201, 240, 0.12); border-color: rgba(76, 201, 240, 0.2); }
.page-hero--recognition .page-hero-icon { color: #fbbf24; background: rgba(251, 191, 36, 0.12); border-color: rgba(251, 191, 36, 0.2); }
.page-hero--media .page-hero-icon { color: #f472b6; background: rgba(244, 114, 182, 0.12); border-color: rgba(244, 114, 182, 0.2); }

.page-hero-icon :deep(.v-icon) { font-size: 15px !important; }

.page-hero-text {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  flex: 0 1 auto;
}

.page-hero-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 15.5px;
  font-weight: 680;
  line-height: 1.3;
  letter-spacing: -0.01em;
}

.page-hero-desc {
  margin: 0;
  color: var(--text-muted);
  font-size: 11.5px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-hero-title,
.page-hero-desc {
  max-width: 100%;
  overflow-wrap: anywhere;
}

.page-hero-status {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.page-hero-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  margin-left: auto;
  justify-content: flex-end;
}

.page-hero-actions :deep(.v-btn) {
  min-height: 32px;
}

.page-hero-meta {
  flex-basis: 100%;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--text-muted);
}

@media (max-width: 768px) {
  .page-hero { gap: 8px; }
  .page-hero-desc { display: none; }
}

@media (max-width: 480px) {
  .page-hero-actions {
    width: 100%;
    margin-left: 0;
  }

  .page-hero-actions :deep(.v-btn) {
    flex: 1 1 140px;
  }
}
</style>
