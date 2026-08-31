<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useGlobalState } from "@/composables/useGlobalState";
import { logsApi, transferApi } from "@/api";
import type { TransferTask } from "@/api/types";
import { displayName, formatBytes } from "@/utils/format";
import PageHero from "@/components/PageHero.vue";
import GlassCard from "@/components/GlassCard.vue";
import StatTile from "@/components/StatTile.vue";

const router = useRouter();
const { state, loadStatus, notifySuccess, notifyError } = useGlobalState();

// ===== 状态统计 =====
const pan = computed(() => state.status?.pan123);
const submission = computed(() => state.submissionStatus);

const panName = computed(() => displayName(pan.value?.profile || null, pan.value?.user || ""));
const spaceText = computed(() => {
  const used = formatBytes(pan.value?.profile?.spaceUsed);
  const total = formatBytes(pan.value?.profile?.spacePermanent);
  return used && total ? `${used} / ${total}` : used || total || "";
});

const transferTasks = ref<TransferTask[]>([]);
const queueCount = computed(
  () => transferTasks.value.filter((task) => task.status === "queued" || task.status === "running").length
);
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

// ===== 日志 =====
const LOG_FETCH_LIMIT = 2000;
const RENDER_LIMIT = 800;

interface ParsedLine {
  raw: string;
  time: string;
  logger: string;
  level: string;
  tone: "error" | "warning" | "info" | "muted";
  message: string;
}

const LOG_LINE_RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (\S+) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - ([\s\S]*)$/;

function parseLines(lines: string[]): ParsedLine[] {
  const out: ParsedLine[] = [];
  let lastTone: ParsedLine["tone"] = "muted";
  for (const raw of lines) {
    const match = LOG_LINE_RE.exec(raw);
    if (match) {
      const level = match[3];
      const tone: ParsedLine["tone"]
        = level === "ERROR" || level === "CRITICAL" ? "error"
          : level === "WARNING" ? "warning" : "info";
      lastTone = tone;
      out.push({ raw, time: match[1], logger: match[2], level, tone, message: match[4] });
    } else {
      // 堆栈等续行：跟随上一条的级别着色
      out.push({ raw, time: "", logger: "", level: "", tone: lastTone, message: raw });
    }
  }
  return out;
}

const rawLogs = ref<string[]>([]);
const renderedSource = ref<string[]>([]);
const paused = ref(false);
const keyword = ref("");
const levelFilter = ref<"all" | "error" | "warning">("all");
const follow = ref(true);
const logBox = ref<HTMLElement | null>(null);
const logError = ref(false);

const parsedLines = computed(() => parseLines(renderedSource.value));
const errorCount = computed(() => parsedLines.value.filter((line) => line.tone === "error").length);
const warningCount = computed(() => parsedLines.value.filter((line) => line.tone === "warning").length);

const levelOptions = computed(() => [
  { value: "all" as const, label: "全部" },
  { value: "error" as const, label: "ERROR", count: errorCount.value },
  { value: "warning" as const, label: "WARNING", count: warningCount.value },
]);

const displayLines = computed(() => {
  let lines = parsedLines.value;
  if (levelFilter.value === "error") lines = lines.filter((line) => line.tone === "error");
  else if (levelFilter.value === "warning") lines = lines.filter((line) => line.tone === "warning");
  const kw = (keyword.value || "").trim().toLowerCase();
  if (kw) lines = lines.filter((line) => line.raw.toLowerCase().includes(kw));
  return lines.slice(-RENDER_LIMIT);
});

watch(rawLogs, (lines) => {
  if (!paused.value) renderedSource.value = lines;
}, { immediate: true });

watch(displayLines, async () => {
  if (!follow.value) return;
  await nextTick();
  const box = logBox.value;
  if (box) box.scrollTop = box.scrollHeight;
});

function onLogScroll() {
  const box = logBox.value;
  if (!box) return;
  follow.value = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
}

function jumpToLatest() {
  follow.value = true;
  void nextTick(() => {
    const box = logBox.value;
    if (box) box.scrollTop = box.scrollHeight;
  });
}

async function fetchLogs() {
  try {
    const payload = await logsApi.tail(LOG_FETCH_LIMIT);
    rawLogs.value = Array.isArray(payload.logs) ? payload.logs : [];
    logError.value = false;
  } catch {
    logError.value = true;
  }
}

async function copyLogs() {
  try {
    const text = displayLines.value.map((line) => line.raw).join("\n");
    await navigator.clipboard.writeText(text || "暂无日志");
    notifySuccess("日志已复制");
  } catch {
    notifyError("复制失败，请手动选择文本");
  }
}

function clearLogView() {
  rawLogs.value = [];
  renderedSource.value = [];
}

async function refreshAll() {
  await Promise.all([loadStatus(), loadTransferTasks(), fetchLogs()]);
}

// ===== 轮询与可见性 =====
let logPollTimer: number | undefined;
let taskPollTimer: number | undefined;

function onVisibilityChange() {
  if (!document.hidden) void fetchLogs();
}

onMounted(() => {
  void fetchLogs();
  void loadTransferTasks();
  logPollTimer = window.setInterval(() => {
    if (!document.hidden) void fetchLogs();
  }, 2000);
  taskPollTimer = window.setInterval(() => {
    if (!document.hidden) void loadTransferTasks();
  }, 15_000);
  document.addEventListener("visibilitychange", onVisibilityChange);
});

onBeforeUnmount(() => {
  if (logPollTimer) window.clearInterval(logPollTimer);
  if (taskPollTimer) window.clearInterval(taskPollTimer);
  document.removeEventListener("visibilitychange", onVisibilityChange);
});
</script>

<template>
  <div class="page">
    <PageHero
      title="控制台"
      desc="服务状态与后端实时日志，配置入口在左侧导航。"
      icon="mdi-console"
      group="dashboard"
    >
      <template #actions>
        <v-btn variant="text" :loading="state.loading" prepend-icon="mdi-refresh" @click="refreshAll">
          刷新
        </v-btn>
      </template>
    </PageHero>

    <div class="stat-grid">
      <StatTile
        label="后端服务"
        :value="state.status ? '运行中' : '连接中…'"
        icon="mdi-server"
        :tone="state.status ? 'success' : 'error'"
      />
      <StatTile
        label="123 绑定"
        :value="pan?.authenticated ? panName : '未绑定'"
        icon="mdi-cloud-check-outline"
        :tone="pan?.authenticated ? 'success' : 'warning'"
        :hint="pan?.authenticated ? (spaceText || '账号已连接') : '设置页可绑定'"
      />
      <StatTile
        label="搬运队列"
        :value="queueCount"
        icon="mdi-cloud-sync"
        :tone="queueCount ? 'info' : 'success'"
        :hint="`共 ${transferTasks.length} 个任务`"
      />
      <StatTile
        label="投稿草稿"
        :value="submission?.draftCount ?? '--'"
        icon="mdi-robot"
        :tone="submission?.botConfigured ? 'success' : 'info'"
        :hint="submission?.botConfigured ? '机器人已配置' : '机器人待配置'"
      />
    </div>

    <GlassCard title="实时日志" desc="后端统一日志流，每 2 秒自动刷新。" icon="mdi-console-line" :hover="false" :padded="false" class="console-log-card">
      <div class="console-toolbar">
        <div class="console-levels" role="group" aria-label="日志级别过滤">
          <button
            v-for="option in levelOptions"
            :key="option.value"
            type="button"
            class="console-level-chip"
            :class="{ active: levelFilter === option.value }"
            :data-level="option.value"
            @click="levelFilter = option.value"
          >
            {{ option.label }}
            <span v-if="option.value !== 'all' && option.count" class="console-level-count">{{ option.count }}</span>
          </button>
        </div>
        <v-text-field
          v-model="keyword"
          class="console-search"
          density="compact"
          hide-details
          clearable
          placeholder="关键字过滤…"
          prepend-inner-icon="mdi-magnify"
        />
        <div class="console-actions">
          <v-btn variant="text" size="small" :icon="paused ? 'mdi-play' : 'mdi-pause'" :title="paused ? '恢复跟随' : '暂停渲染'" @click="paused = !paused" />
          <v-btn variant="text" size="small" icon="mdi-content-copy" title="复制当前日志" @click="copyLogs" />
          <v-btn variant="text" size="small" icon="mdi-delete-outline" title="清空视图" @click="clearLogView" />
        </div>
      </div>

      <div ref="logBox" class="console-log-box" @scroll.passive="onLogScroll">
        <div v-if="displayLines.length === 0" class="empty-state">
          <v-icon icon="mdi-text-box-outline" size="26" />
          <span>{{ logError ? "日志读取失败，后端可能尚未就绪，稍后会自动重试。" : "暂无日志。启动任务或触发一次刷新后，这里会实时显示后端输出。" }}</span>
        </div>
        <template v-else>
          <div
            v-for="(line, index) in displayLines"
            :key="index"
            class="console-log-row"
            :data-level="line.tone"
            :class="{ 'console-log-row--cont': !line.time }"
          >
            <span v-if="line.time" class="console-log-time">{{ line.time }}</span>
            <span v-if="line.logger" class="console-log-logger">{{ line.logger }}</span>
            <span v-if="line.level" class="console-log-level">{{ line.level }}</span>
            <span class="console-log-message">{{ line.message }}</span>
          </div>
        </template>

        <button v-if="!follow && displayLines.length" type="button" class="console-jump" @click="jumpToLatest">
          <v-icon icon="mdi-arrow-down" size="15" />
          回到最新
        </button>
      </div>
    </GlassCard>

    <div class="section-grid">
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
.console-log-card :deep(.glass-card-body) { display: flex; flex-direction: column; }

.console-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 16px 10px;
}

.console-levels { display: flex; gap: 6px; }
.console-level-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border);
  background: var(--surface-subtle);
  color: var(--text-muted);
  font-size: 11.5px;
  font-weight: 650;
  letter-spacing: 0.02em;
  cursor: pointer;
  transition: border-color var(--transition), background var(--transition), color var(--transition);
}
.console-level-chip:hover { border-color: var(--glass-border-1); color: var(--text-primary); }
.console-level-chip.active { color: #fff; background: var(--grad-accent); border-color: transparent; }
.console-level-chip[data-level="error"].active { background: var(--grad-error); }
.console-level-chip[data-level="warning"].active { background: var(--grad-warning); }
.console-level-count {
  font-family: var(--font-mono);
  font-size: 10.5px;
  background: rgba(255, 255, 255, 0.22);
  border-radius: var(--radius-pill);
  padding: 0 6px;
}

.console-search { flex: 1; min-width: 160px; max-width: 340px; }
.console-actions { display: flex; gap: 2px; margin-left: auto; }

.console-log-box {
  position: relative;
  height: clamp(360px, 54vh, 680px);
  overflow-y: auto;
  overflow-x: hidden;
  border-top: 1px solid var(--border);
  background: rgba(4, 5, 12, 0.55);
  padding: 12px 16px 20px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.7;
}
[data-theme="light"] .console-log-box { background: rgba(20, 24, 48, 0.9); }

.console-log-row {
  display: flex;
  gap: 8px;
  padding: 0 2px;
  border-radius: 4px;
  color: rgba(214, 226, 255, 0.88);
}
.console-log-row:hover { background: rgba(255, 255, 255, 0.045); }
.console-log-row--cont { padding-left: 18px; }

.console-log-time { flex: 0 0 auto; color: rgba(148, 163, 206, 0.66); }
.console-log-logger {
  flex: 0 1 auto;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(125, 170, 255, 0.75);
}
.console-log-level { flex: 0 0 58px; font-weight: 700; }
.console-log-message { flex: 1; min-width: 0; white-space: pre-wrap; word-break: break-all; }

.console-log-row[data-level="error"] .console-log-level,
.console-log-row[data-level="error"] .console-log-message { color: var(--error); }
.console-log-row[data-level="warning"] .console-log-level,
.console-log-row[data-level="warning"] .console-log-message { color: var(--warning); }

.console-jump {
  position: sticky;
  bottom: 10px;
  left: 50%;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 12px auto 0;
  padding: 7px 16px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--glass-border-1);
  background: var(--surface-card);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
  box-shadow: var(--shadow-lift);
  -webkit-backdrop-filter: blur(14px);
  backdrop-filter: blur(14px);
}

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
