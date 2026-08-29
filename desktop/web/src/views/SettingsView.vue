<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useTheme } from "@/composables/useTheme";
import { useGlobalState } from "@/composables/useGlobalState";
import PageHero from "@/components/PageHero.vue";
import GlassCard from "@/components/GlassCard.vue";
import FormField from "@/components/FormField.vue";

interface DesktopInfo {
  isDesktop: boolean;
  platform: string;
  dataDir: string;
  port: number;
  fixedPort: number | null;
  versions: {
    app: string;
    electron: string;
  };
}

const { theme, setTheme } = useTheme();
const { state, notifySuccess, notifyError } = useGlobalState();

const desktopInfo = ref<DesktopInfo | null>(null);
const backendVersion = computed(() => (state.status ? "已连接" : "未连接"));

const themeOptions = [
  { value: "auto" as const, label: "跟随系统", icon: "mdi-theme-light-dark", desc: "与操作系统保持一致" },
  { value: "light" as const, label: "浅色", icon: "mdi-white-balance-sunny", desc: "明亮的冰玻璃" },
  { value: "dark" as const, label: "深色", icon: "mdi-moon-waning-crescent", desc: "深空极光玻璃" },
];

// ===== 服务端口 =====
const portMode = ref<"auto" | "fixed">("auto");
const fixedPortInput = ref("");
const savingPort = ref(false);

// ===== 运行日志 =====
const logLines = ref<string[]>([]);
const logPaused = ref(false);
const logBox = ref<HTMLElement | null>(null);
let detachLog: (() => void) | null = null;

const logText = computed(() => (logLines.value.length ? logLines.value.join("\n") : "暂无日志。启动应用或触发一次刷新后，这里会实时显示后端输出。"));

async function openDataDir() {
  const api = desktopApi();
  if (api?.openDataDir) await api.openDataDir();
}

function desktopApi() {
  return (window as unknown as {
    cloud123?: {
      getInfo?: () => Promise<DesktopInfo>;
      openDataDir?: () => Promise<string>;
      getLogs?: () => Promise<string[]>;
      onLogLines?: (callback: (lines: string[]) => void) => void;
      getPortConfig?: () => Promise<{ port: number | null }>;
      setPortConfig?: (payload: { port: number | null }) => Promise<{ port: number | null }>;
    };
  }).cloud123;
}

async function savePortConfig() {
  const api = desktopApi();
  if (!api?.setPortConfig) return;
  savingPort.value = true;
  try {
    const trimmed = fixedPortInput.value.trim();
    const port = portMode.value === "fixed" && trimmed ? Number(trimmed) : null;
    if (port !== null && (!Number.isInteger(port) || port < 1024 || port > 65535)) {
      notifyError("端口需为 1024–65535 的整数");
      return;
    }
    await api.setPortConfig({ port });
    notifySuccess("端口设置已保存，重启应用后生效");
  } catch (error) {
    notifyError(`保存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    savingPort.value = false;
  }
}

async function copyLogs() {
  try {
    await navigator.clipboard.writeText(logText.value);
    notifySuccess("日志已复制");
  } catch {
    notifyError("复制失败，请手动选择文本");
  }
}

function clearLogView() {
  logLines.value = [];
}

watch(logLines, async () => {
  if (logPaused.value) return;
  await nextTick();
  const box = logBox.value;
  if (box) box.scrollTop = box.scrollHeight;
});

onMounted(async () => {
  const api = desktopApi();
  if (api?.getInfo) {
    try {
      desktopInfo.value = await api.getInfo();
      if (desktopInfo.value?.fixedPort) {
        portMode.value = "fixed";
        fixedPortInput.value = String(desktopInfo.value.fixedPort);
      }
    } catch {
      desktopInfo.value = null;
    }
  }
  if (api?.getLogs) {
    try {
      logLines.value = await api.getLogs();
    } catch { /* browser mode */ }
  }
  if (api?.onLogLines) {
    api.onLogLines((lines) => {
      logLines.value.push(...lines);
      if (logLines.value.length > 800) logLines.value.splice(0, logLines.value.length - 800);
    });
    detachLog = () => { /* listener lives with the window */ };
  }
});

onBeforeUnmount(() => {
  detachLog?.();
});
</script>

<template>
  <div class="page">
    <PageHero title="设置" desc="外观、服务端口、运行日志与应用信息。" icon="mdi-cog-outline" group="dashboard" />

    <div class="section-grid">
      <GlassCard title="外观" desc="液态玻璃支持浅色与深色两种质感。" icon="mdi-palette" :hover="false">
        <div class="theme-options">
          <button
            v-for="option in themeOptions"
            :key="option.value"
            type="button"
            class="theme-option-card"
            :class="{ active: theme.preference === option.value }"
            @click="setTheme(option.value)"
          >
            <span class="theme-option-icon"><v-icon :icon="option.icon" size="20" /></span>
            <span class="theme-option-copy">
              <strong>{{ option.label }}</strong>
              <small>{{ option.desc }}</small>
            </span>
            <v-icon v-if="theme.preference === option.value" icon="mdi-check-circle" size="20" class="theme-check" />
          </button>
        </div>
      </GlassCard>

      <GlassCard title="服务端口" desc="后端默认只监听本机并自动选择空闲端口。" icon="mdi-lan" :hover="false">
        <FormField label="当前端口">
          <code class="settings-path">{{ desktopInfo ? `${desktopInfo.port}${desktopInfo.fixedPort ? "（固定）" : "（自动分配）"}` : "仅桌面应用可用" }}</code>
        </FormField>
        <FormField hint="端口冲突或需要端口转发时，可固定为指定端口；保存后重启应用生效。">
          <div class="port-row">
            <v-btn-toggle v-model="portMode" mandatory density="compact" class="port-toggle">
              <v-btn value="auto">自动</v-btn>
              <v-btn value="fixed">固定端口</v-btn>
            </v-btn-toggle>
            <v-text-field
              v-if="portMode === 'fixed'"
              v-model="fixedPortInput"
              type="number"
              density="compact"
              hide-details
              placeholder="1024–65535"
              class="port-input"
            />
            <v-btn
              variant="tonal"
              size="small"
              :loading="savingPort"
              prepend-icon="mdi-content-save"
              :disabled="!desktopInfo?.isDesktop"
              @click="savePortConfig"
            >
              保存
            </v-btn>
          </div>
        </FormField>
      </GlassCard>

      <GlassCard title="数据目录" desc="配置、会话与任务数据都保存在本地。" icon="mdi-database-outline" :hover="false">
        <FormField label="数据目录">
          <code class="settings-path">{{ desktopInfo?.dataDir || "浏览器模式：数据由后端 DATA_DIR 决定" }}</code>
        </FormField>
        <FormField hint="包含 cloud123.db 数据库与应用配置。">
          <v-btn
            v-if="desktopInfo?.isDesktop"
            variant="tonal"
            size="small"
            prepend-icon="mdi-folder-open-outline"
            @click="openDataDir"
          >
            打开数据文件夹
          </v-btn>
          <span v-else class="muted" style="font-size: 12px">桌面应用内可用一键打开</span>
        </FormField>
      </GlassCard>

      <GlassCard
        title="运行日志"
        desc="后端实时输出（仅保留最近 800 行，不写入磁盘）。"
        icon="mdi-text-box-outline"
        :hover="false"
        class="log-card"
      >
        <template #actions>
          <v-btn variant="text" size="small" :icon="logPaused ? 'mdi-play' : 'mdi-pause'" :title="logPaused ? '恢复跟随' : '暂停跟随'" @click="logPaused = !logPaused" />
          <v-btn variant="text" size="small" icon="mdi-content-copy" title="复制日志" @click="copyLogs" />
          <v-btn variant="text" size="small" icon="mdi-delete-outline" title="清空视图" @click="clearLogView" />
        </template>
        <div ref="logBox" class="log-box">
          <pre class="log-text">{{ logText }}</pre>
        </div>
        <p v-if="!desktopInfo?.isDesktop" class="muted" style="font-size: 11.5px; margin: 8px 2px 0">
          浏览器模式下不展示日志，请使用桌面应用查看。
        </p>
      </GlassCard>

      <GlassCard title="关于" desc="123Cloud — Telegram 投稿与 115 协作工作台。" icon="mdi-information-outline" :hover="false">
        <div class="kv-grid">
          <div class="kv-tile">
            <div class="kv-tile-label">应用版本</div>
            <div class="kv-tile-value">{{ desktopInfo?.versions?.app || "1.0.0（浏览器模式）" }}</div>
          </div>
          <div class="kv-tile">
            <div class="kv-tile-label">后端服务</div>
            <div class="kv-tile-value">{{ backendVersion }}</div>
          </div>
          <div class="kv-tile">
            <div class="kv-tile-label">运行平台</div>
            <div class="kv-tile-value">{{ desktopInfo?.platform || "browser" }}</div>
          </div>
          <div v-if="desktopInfo?.versions?.electron" class="kv-tile">
            <div class="kv-tile-label">Electron</div>
            <div class="kv-tile-value">{{ desktopInfo.versions.electron }}</div>
          </div>
        </div>
      </GlassCard>
    </div>
  </div>
</template>

<style scoped>
.theme-options { display: flex; flex-direction: column; gap: 10px; }

.theme-option-card {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 13px 14px;
  border-radius: var(--radius-control);
  border: 1px solid var(--border);
  background: var(--surface-subtle);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--transition), background var(--transition), box-shadow var(--transition);
}
.theme-option-card:hover { border-color: var(--glass-border-1); background: var(--bg-hover); }
.theme-option-card.active {
  border-color: rgba(124, 92, 255, 0.5);
  background: linear-gradient(130deg, rgba(124, 92, 255, 0.18), rgba(76, 201, 240, 0.08));
  box-shadow: 0 0 0 3px rgba(124, 92, 255, 0.12), inset 0 1px 0 var(--glass-highlight);
}

.theme-option-icon {
  width: 36px; height: 36px;
  flex: 0 0 36px;
  border-radius: 11px;
  display: grid;
  place-items: center;
  background: var(--accent-soft);
  color: var(--accent-2);
}
.theme-option-card.active .theme-option-icon {
  background: var(--grad-accent);
  color: #fff;
  box-shadow: 0 6px 14px rgba(124, 92, 255, 0.3);
}

.theme-option-copy { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.theme-option-copy strong { font-size: 13px; font-weight: 640; color: var(--text-primary); }
.theme-option-copy small { color: var(--text-muted); font-size: 11.5px; }
.theme-check { color: var(--accent-2); }

.settings-path {
  display: block;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 9px 11px;
  word-break: break-all;
}

.port-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.port-toggle { background: var(--surface-subtle); border-radius: var(--radius-pill); }
.port-input { width: 150px; }

.log-card { grid-column: 1 / -1; }
.log-box {
  height: 260px;
  overflow-y: auto;
  border-radius: var(--radius-control);
  border: 1px solid var(--border);
  background: rgba(4, 5, 12, 0.55);
  padding: 12px 14px;
}
[data-theme="light"] .log-box { background: rgba(20, 24, 48, 0.9); }
.log-text {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.65;
  color: rgba(214, 226, 255, 0.88);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
