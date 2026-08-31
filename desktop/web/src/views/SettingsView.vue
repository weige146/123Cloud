<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useTheme } from "@/composables/useTheme";
import { useGlobalState } from "@/composables/useGlobalState";
import { useUpdater } from "@/composables/useUpdater";
import { adminApi } from "@/api";
import { displayName, formatBytes } from "@/utils/format";
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
const { state, notifySuccess, notifyError, confirm, loadStatus } = useGlobalState();

const desktopInfo = ref<DesktopInfo | null>(null);
const backendVersion = computed(() => (state.status ? "已连接" : "未连接"));

// ===== 123 网盘绑定 =====
const pan123 = computed(() => state.status?.pan123);
const pan123Authenticated = computed(() => Boolean(pan123.value?.authenticated));
const pan123Profile = computed(() => pan123.value?.profile || null);
const pan123Name = computed(() => displayName(pan123Profile.value || null, pan123.value?.user || ""));
const pan123Space = computed(() => {
  const profile = pan123Profile.value;
  if (!profile) return "";
  const used = formatBytes(profile.spaceUsed);
  const total = formatBytes(profile.spacePermanent);
  if (used && total) return `${used} / ${total}`;
  return used || (total ? `总容量 ${total}` : "");
});

const bindForm = ref({ user: "", password: "" });
const showBindPassword = ref(false);
const binding = ref(false);
const unbinding = ref(false);

async function bindPan123() {
  const user = bindForm.value.user.trim();
  const password = bindForm.value.password;
  if (!user || !password) {
    notifyError("请输入 123 云盘账号和密码");
    return;
  }
  binding.value = true;
  try {
    await adminApi.login(user, password, true);
    bindForm.value = { user: "", password: "" };
    notifySuccess("123 网盘绑定成功");
    await loadStatus();
  } catch (error) {
    notifyError(`绑定失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    binding.value = false;
  }
}

async function unbindPan123() {
  const ok = await confirm("解除绑定后，投稿归属判断与他人分享的自动转存将不可用，确定解除吗？", "解除绑定 123 网盘");
  if (!ok) return;
  unbinding.value = true;
  try {
    await adminApi.logout();
    notifySuccess("已解除绑定");
    await loadStatus();
  } catch (error) {
    notifyError(`解除绑定失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    unbinding.value = false;
  }
}

const themeOptions = [
  { value: "auto" as const, label: "跟随系统", icon: "mdi-theme-light-dark", desc: "与操作系统保持一致" },
  { value: "light" as const, label: "浅色", icon: "mdi-white-balance-sunny", desc: "明亮的冰玻璃" },
  { value: "dark" as const, label: "深色", icon: "mdi-moon-waning-crescent", desc: "深空极光玻璃" },
];

// ===== 软件更新 =====
const updater = useUpdater();
const appVersion = computed(() => desktopInfo.value?.versions?.app || "");

const updateStatusText = computed(() => {
  const version = updater.state.info?.version || "";
  switch (updater.state.status) {
    case "checking":
      return "正在检查更新…";
    case "downloading":
      return updater.state.percent
        ? `正在下载新版本 ${version}（${updater.state.percent}%）`
        : `正在下载新版本 ${version}…`;
    case "downloaded":
      return `新版本 ${version} 已就绪，点击「重启并安装」完成升级。`;
    case "installing":
      return "正在安装更新，应用即将重启…";
    case "none":
      return `已是最新版本${version ? `（${version}）` : ""}。`;
    case "update-manual":
      return `发现新版本 ${version}；该版本未包含自动更新组件，请到发布页下载安装。`;
    case "error":
      return `检查更新失败：${updater.state.error || "网络不可用"}`;
    default:
      return "应用启动后会自动检查 GitHub 新版本。";
  }
});

const showOpenReleases = computed(
  () => updater.state.status === "error" || updater.state.status === "update-manual",
);

// ===== 服务端口 =====
const portMode = ref<"auto" | "fixed">("auto");
const fixedPortInput = ref("");
const savingPort = ref(false);

async function openDataDir() {
  const api = desktopApi();
  if (api?.openDataDir) await api.openDataDir();
}

function desktopApi() {
  return (window as unknown as {
    cloud123?: {
      getInfo?: () => Promise<DesktopInfo>;
      openDataDir?: () => Promise<string>;
      getPortConfig?: () => Promise<{ port: number | null }>;
      setPortConfig?: (payload: { port: number | null }) => Promise<{ port: number | null }>;
      relaunchApp?: () => Promise<boolean>;
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
    notifySuccess(port ? `端口已固定为 ${port}，应用即将重启生效` : "已恢复自动分配端口，应用即将重启");
    // 端口在启动时绑定，保存后立即重启应用让配置马上生效
    setTimeout(() => { void api.relaunchApp?.(); }, 900);
  } catch (error) {
    notifyError(`保存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    savingPort.value = false;
  }
}

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
});
</script>

<template>
  <div class="page">
    <PageHero title="设置" desc="外观、服务端口、数据目录与应用信息。" icon="mdi-cog-outline" group="dashboard" />

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

      <GlassCard title="123 网盘" desc="投稿归属判断与他人分享自动转存都需要绑定 123 云盘账号，配置一次即可。" icon="mdi-link-variant" :hover="false">
        <div v-if="pan123Authenticated" class="pan-bind">
          <div class="pan-bind-info">
            <span class="pan-bind-avatar">
              <img v-if="pan123Profile?.headImage" :src="pan123Profile.headImage" alt="" />
              <v-icon v-else icon="mdi-account" size="20" />
            </span>
            <span class="pan-bind-copy">
              <strong>{{ pan123Name }}</strong>
              <small>{{ pan123Space || "账号已连接" }}</small>
            </span>
            <span class="pan-bind-badge">已绑定</span>
          </div>
          <v-btn variant="tonal" color="error" size="small" prepend-icon="mdi-link-variant-off" :loading="unbinding" @click="unbindPan123">
            解除绑定
          </v-btn>
        </div>

        <form v-else class="pan-bind-form" @submit.prevent="bindPan123">
          <FormField label="账号">
            <v-text-field
              v-model="bindForm.user"
              placeholder="123 云盘账号"
              prepend-inner-icon="mdi-account-outline"
              variant="outlined"
              density="comfortable"
              hide-details
              autocomplete="username"
            />
          </FormField>
          <FormField label="密码">
            <v-text-field
              v-model="bindForm.password"
              :type="showBindPassword ? 'text' : 'password'"
              placeholder="123 云盘密码"
              prepend-inner-icon="mdi-lock-outline"
              :append-inner-icon="showBindPassword ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
              variant="outlined"
              density="comfortable"
              hide-details
              autocomplete="current-password"
              @click:append-inner="showBindPassword = !showBindPassword"
            />
          </FormField>
          <v-btn color="primary" type="submit" :loading="binding" prepend-icon="mdi-link-variant">
            绑定 123 网盘
          </v-btn>
          <p class="muted" style="font-size: 11.5px; margin: 2px 0 0">
            凭据只保存在本机，用于判断投稿是否属于你，以及自动转存他人分享的 115 资源。
          </p>
        </form>
      </GlassCard>

      <GlassCard title="服务端口" desc="后端默认只监听本机并自动选择空闲端口。" icon="mdi-lan" :hover="false">
        <FormField label="当前端口">
          <code class="settings-path">{{ desktopInfo ? `${desktopInfo.port}${desktopInfo.fixedPort ? "（固定）" : "（自动分配）"}` : "仅桌面应用可用" }}</code>
        </FormField>
        <FormField hint="端口冲突或需要端口转发时，可固定为指定端口；保存后应用自动重启并生效。">
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
        v-if="updater.available"
        title="软件更新"
        desc="自动检测 GitHub 新版本；下载后重启即可升级，配置与数据不受影响。"
        icon="mdi-cellphone-arrow-down"
        :hover="false"
      >
        <div class="update-row">
          <div class="update-copy">
            <strong>当前版本 {{ appVersion || "未知" }}</strong>
            <small>{{ updateStatusText }}</small>
          </div>
          <div class="update-actions">
            <v-btn
              v-if="updater.state.status === 'downloaded'"
              color="primary"
              prepend-icon="mdi-restart"
              @click="updater.install()"
            >
              重启并安装
            </v-btn>
            <v-btn
              v-if="showOpenReleases"
              :color="updater.state.status === 'update-manual' ? 'primary' : undefined"
              :variant="updater.state.status === 'update-manual' ? 'flat' : 'text'"
              href="https://github.com/weige146/123Cloud/releases/latest"
              target="_blank"
              prepend-icon="mdi-open-in-new"
            >
              打开发布页
            </v-btn>
            <v-btn
              v-if="updater.state.status !== 'downloaded' && updater.state.status !== 'update-manual'"
              variant="tonal"
              prepend-icon="mdi-refresh"
              :loading="updater.state.status === 'checking' || updater.state.status === 'downloading'"
              @click="updater.check()"
            >
              检查更新
            </v-btn>
          </div>
        </div>
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

.pan-bind { display: flex; flex-direction: column; gap: 14px; }
.pan-bind-info {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--radius-control);
  border: 1px solid var(--border);
  background: var(--surface-subtle);
}
.pan-bind-avatar {
  width: 38px; height: 38px;
  flex: 0 0 38px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: var(--accent-soft);
  color: var(--accent-2);
}
.pan-bind-avatar img { width: 100%; height: 100%; object-fit: cover; }
.pan-bind-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.pan-bind-copy strong {
  font-size: 13px; font-weight: 640; color: var(--text-primary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.pan-bind-copy small { color: var(--text-muted); font-size: 11.5px; }
.pan-bind-badge {
  flex: 0 0 auto;
  font-size: 11px; font-weight: 600;
  color: var(--success, #2ecc71);
  background: rgba(46, 204, 113, 0.12);
  border: 1px solid rgba(46, 204, 113, 0.3);
  border-radius: var(--radius-pill);
  padding: 3px 10px;
}
.pan-bind-form { display: flex; flex-direction: column; gap: 12px; }

.port-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.port-toggle { background: var(--surface-subtle); border-radius: var(--radius-pill); }
.port-input { width: 150px; }

.update-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
.update-copy { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.update-copy strong { font-size: 13px; font-weight: 640; color: var(--text-primary); }
.update-copy small { color: var(--text-muted); font-size: 11.5px; }
.update-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
