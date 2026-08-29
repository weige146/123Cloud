<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useGlobalState } from "@/composables/useGlobalState";
import { useResponsive } from "@/composables/useResponsive";
import { transferApi } from "@/api";
import type { TransferTask } from "@/api/types";
import PageHero from "@/components/PageHero.vue";
import GlassCard from "@/components/GlassCard.vue";
import { displayName, formatBytes } from "@/utils/format";

const router = useRouter();
const { state } = useGlobalState();
const { isMobile } = useResponsive();

const transferTasks = ref<TransferTask[]>([]);
let pollTimer: number | undefined;

const pan = computed(() => state.status?.pan123);
const profile = computed(() => pan.value?.profile);
const panName = computed(() => displayName(profile.value || null, pan.value?.user || ""));
const spaceText = computed(() => {
  const used = formatBytes(profile.value?.spaceUsed);
  const total = formatBytes(profile.value?.spacePermanent);
  return used && total ? `${used} / ${total}` : used || total || "--";
});
const capabilities = computed(() => state.status?.capabilities);
const submission = computed(() => state.submissionStatus);

const recentTasks = computed(() =>
  transferTasks.value
    .slice()
    .sort((a, b) => String(b.updatedAt || b.createdAt || "").localeCompare(String(a.updatedAt || a.createdAt || "")))
    .slice(0, 5)
);

const taskStatusTone = (status?: string) => {
  if (status === "success") return "success";
  if (status === "failed") return "error";
  if (status === "running") return "running";
  if (status === "partial") return "partial";
  return "queued";
};

async function loadTransferTasks() {
  try {
    transferTasks.value = await transferApi.tasks(20);
  } catch {
    transferTasks.value = [];
  }
}

onMounted(() => {
  loadTransferTasks();
  pollTimer = window.setInterval(loadTransferTasks, 15_000);
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer);
});
</script>

<template>
  <div class="page">
    <PageHero
      title="欢迎使用 123Cloud"
      desc="Telegram 投稿与 115 协作工作台。"
      icon="mdi-view-dashboard-outline"
      group="dashboard"
    >
      <template #actions>
        <v-btn variant="text" :loading="state.loading" prepend-icon="mdi-refresh" @click="() => loadTransferTasks()">
          刷新
        </v-btn>
      </template>
    </PageHero>

    <div class="stat-grid">
      <div class="stat-tile" data-tone="success">
        <div class="stat-tile-icon"><v-icon icon="mdi-server" /></div>
        <div class="stat-tile-body">
          <div class="stat-tile-value">{{ state.status ? "运行中" : "连接中…" }}</div>
          <div class="stat-tile-label">后端服务</div>
        </div>
      </div>
      <div class="stat-tile" :data-tone="pan?.authenticated ? 'success' : 'warning'">
        <div class="stat-tile-icon"><v-icon icon="mdi-cloud-check" /></div>
        <div class="stat-tile-body">
          <div class="stat-tile-value">{{ pan?.authenticated ? panName : "未登录" }}</div>
          <div class="stat-tile-label">123 云盘会话</div>
          <div v-if="pan?.authenticated" class="stat-tile-hint">{{ spaceText }}</div>
        </div>
      </div>
      <div class="stat-tile" :data-tone="submission?.botConfigured ? 'success' : 'info'">
        <div class="stat-tile-icon"><v-icon icon="mdi-robot" /></div>
        <div class="stat-tile-body">
          <div class="stat-tile-value">{{ submission?.botConfigured ? "已配置" : "待配置" }}</div>
          <div class="stat-tile-label">投稿机器人</div>
          <div v-if="submission" class="stat-tile-hint">草稿 {{ submission.draftCount }} · 授权 {{ submission.allowedUserCount }} 人</div>
        </div>
      </div>
      <div class="stat-tile" :data-tone="capabilities?.transferConfigured ? 'success' : 'info'">
        <div class="stat-tile-icon"><v-icon icon="mdi-cloud-sync" /></div>
        <div class="stat-tile-body">
          <div class="stat-tile-value">{{ capabilities?.transferConfigured ? "已就绪" : "待配置" }}</div>
          <div class="stat-tile-label">115 搬运</div>
          <div class="stat-tile-hint">队列中 {{ transferTasks.filter((task) => task.status === "queued" || task.status === "running").length }} 项</div>
        </div>
      </div>
    </div>

    <div :class="isMobile ? 'section-stack' : 'section-grid'">
      <GlassCard title="快捷入口" icon="mdi-flash" :hover="false">
        <div class="quick-grid">
          <button type="button" class="quick-card" @click="router.push('/admin/pan115-cookie')">
            <span class="quick-icon quick-icon--cyan"><v-icon icon="mdi-cookie" size="21" /></span>
            <span class="quick-copy">
              <strong>扫码获取 Cookie</strong>
              <small>115 助手 / 搬运 Cookie 池</small>
            </span>
            <v-icon icon="mdi-chevron-right" size="18" />
          </button>
          <button type="button" class="quick-card" @click="router.push('/admin/transfer')">
            <span class="quick-icon quick-icon--violet"><v-icon icon="mdi-cloud-sync" size="21" /></span>
            <span class="quick-copy">
              <strong>新建搬运任务</strong>
              <small>粘贴 115 分享链接</small>
            </span>
            <v-icon icon="mdi-chevron-right" size="18" />
          </button>
          <button type="button" class="quick-card" @click="router.push('/admin/submission')">
            <span class="quick-icon quick-icon--pink"><v-icon icon="mdi-robot" size="21" /></span>
            <span class="quick-copy">
              <strong>配置投稿机器人</strong>
              <small>Bot Token 与频道路由</small>
            </span>
            <v-icon icon="mdi-chevron-right" size="18" />
          </button>
          <button type="button" class="quick-card" @click="router.push('/admin/display')">
            <span class="quick-icon quick-icon--amber"><v-icon icon="mdi-file-document-outline" size="21" /></span>
            <span class="quick-copy">
              <strong>投稿展示模板</strong>
              <small>标题 / 备注 / 分享按钮</small>
            </span>
            <v-icon icon="mdi-chevron-right" size="18" />
          </button>
        </div>
      </GlassCard>

      <GlassCard title="最近搬运任务" icon="mdi-truck-fast" :hover="false">
        <template #actions>
          <v-btn variant="text" size="small" @click="router.push('/admin/transfer')">查看全部</v-btn>
        </template>
        <div v-if="recentTasks.length === 0" class="empty-state" style="padding: 28px">
          <v-icon icon="mdi-inbox-outline" size="26" />
          <span>还没有搬运任务</span>
        </div>
        <div v-else class="home-task-list">
          <div v-for="task in recentTasks" :key="task.id" class="home-task-row">
            <span class="chip-status" :data-tone="taskStatusTone(task.status)">{{ task.status }}</span>
            <span class="home-task-title">{{ task.title || task.shareUrl || "115 任务" }}</span>
            <span class="home-task-meta">{{ task.doneFiles ?? 0 }}/{{ task.totalFiles ?? 0 }}</span>
          </div>
        </div>
      </GlassCard>
    </div>
  </div>
</template>

<style scoped>
.quick-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
}

.quick-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--transition), background var(--transition), transform var(--transition);
}

.quick-card:hover {
  border-color: var(--glass-border-1);
  background: var(--bg-hover);
  transform: translateY(-1px);
}

.quick-card > .v-icon { color: var(--text-muted); }

.quick-icon {
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: 11px;
  display: grid;
  place-items: center;
  color: #fff;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.4);
}
.quick-icon--cyan { background: linear-gradient(135deg, #0ea5e9, #4cc9f0); }
.quick-icon--violet { background: var(--grad-accent); }
.quick-icon--pink { background: linear-gradient(135deg, #ec4899, #f472b6); }
.quick-icon--amber { background: linear-gradient(135deg, #f59e0b, #fbbf24); }

.quick-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.quick-copy strong { font-size: 13px; font-weight: 640; color: var(--text-primary); }
.quick-copy small { color: var(--text-muted); font-size: 11.5px; }

.home-task-list { display: flex; flex-direction: column; gap: 8px; }
.home-task-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-control);
  border: 1px solid var(--border);
  background: var(--surface-subtle);
}
.home-task-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  color: var(--text-secondary);
}
.home-task-meta {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-muted);
}
</style>
