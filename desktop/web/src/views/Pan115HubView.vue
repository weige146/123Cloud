<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import PageHero from "@/components/PageHero.vue";
import StatTile from "@/components/StatTile.vue";
import SegmentedTabs from "@/components/SegmentedTabs.vue";
import GlassCard from "@/components/GlassCard.vue";
import FormGrid from "@/components/FormGrid.vue";
import { transferApi, pan115HelperApi, pan115CookieApi } from "@/api";
import type { AccountCooldown, Pan115Device, TransferConfig, TransferOfflineTask, TransferTask, TransferTaskFile } from "@/api/types";
import { formatBytes } from "@/utils/format";
import { useGlobalState } from "@/composables/useGlobalState";
import { useResponsive } from "@/composables/useResponsive";

type HubTab = "transfer" | "helper" | "cookie";

const props = withDefaults(
  defineProps<{
    initialTab?: HubTab;
  }>(),
  { initialTab: "transfer" }
);

const route = useRoute();
const router = useRouter();
const { state, loadStatus, ensureSubmissionConfig, writeSubmissionConfig, notifySuccess, notifyError, confirm } = useGlobalState();
const { isMobile } = useResponsive();

const tab = ref<HubTab>(props.initialTab);
const loadedTabs = reactive<Record<HubTab, boolean>>({ transfer: false, helper: false, cookie: false });

const TAB_ROUTES: Record<HubTab, string> = {
  transfer: "/admin/transfer",
  helper: "/admin/pan115-helper",
  cookie: "/admin/pan115-cookie",
};

let syncingRoute = false;

// ============ Transfer state (搬运) ============
const defaultTransferConfig: TransferConfig = {
  enabled: false,
  pan115Cookie: "",
  pan115Cookies: [],
  targetDirId: "0",
  localPath115: "",
  pan115TargetCid: "0",
  pan123OauthApi: "",
  excludeSuffix: "",
  excludeCid: "",
  delete115AfterSuccess: false,
  concurrency: 5,
  pauseEnabled: true,
  pauseTimeZone: "Asia/Shanghai",
  pauseStartHour: 18,
  pauseEndHour: 1,
  downloadMinIntervalMs: 2500,
  downloadMaxAttempts: 5,
  downloadRetryBaseMs: 8000,
  offlinePollMs: 15000,
  offlineMaxPolls: 240,
  progressNotifyIntervalMs: 60000,
};

const transferForm = reactive<TransferConfig>({ ...defaultTransferConfig });
const transferTasks = ref<TransferTask[]>([]);
const offlineTasks = ref<TransferOfflineTask[]>([]);
const offlinePanel = ref(false);
const createTransferDialog = ref(false);
const transferSettingsOpen = ref(false);
const transferDetailOpen = ref(false);
const selectedTransferTask = ref<TransferTask | null>(null);
const showCompleted = ref(localStorage.getItem("transferShowCompleted") === "1");
const submitText = ref("");
const sourceDirId123 = ref("");
const transfer123to115Submitting = ref(false);
const transferLoading = ref(false);
const transferSaving = ref(false);
const submitting = ref(false);
const localSubmitting = ref(false);
const actingTaskId = ref("");
const offlineLoading = ref(false);
const coolingAccounts = ref<AccountCooldown[]>([]);

const timeZones = [
  { title: "北京时间", value: "Asia/Shanghai" },
  { title: "东京时间", value: "Asia/Tokyo" },
  { title: "洛杉矶时间", value: "America/Los_Angeles" },
  { title: "伦敦时间", value: "Europe/London" },
];

const visibleTasks = computed(() => {
  return showCompleted.value ? transferTasks.value : transferTasks.value.filter((task) => task.status !== "success");
});

const taskStats = computed(() => {
  const total = transferTasks.value.length;
  const running = transferTasks.value.filter((task) => task.status === "running").length;
  const queued = transferTasks.value.filter((task) => task.status === "queued").length;
  const failed = transferTasks.value.filter((task) => task.status === "failed" || task.status === "partial").length;
  const success = transferTasks.value.filter((task) => task.status === "success").length;
  return { total, running, queued, failed, success };
});

const coolingText = computed(() => {
  if (!coolingAccounts.value.length) return "";
  const minutes = Math.min(...coolingAccounts.value.map((item) => item.remainingMinutes));
  return `${coolingAccounts.value.length} 个账号冷却中 · 剩 ${minutes} 分钟`;
});

const coolingNames = computed(() =>
  coolingAccounts.value.map((item) => `${item.name}（剩 ${item.remainingMinutes} 分钟）`).join("\n")
);

function applyTransferConfig(config: Partial<TransferConfig>) {
  Object.assign(transferForm, { ...defaultTransferConfig, ...config });
  transferForm.pan115Cookies = Array.isArray(config.pan115Cookies) ? [...config.pan115Cookies] : cookieLines(config.pan115Cookie);
  transferForm.pan115Cookie = transferForm.pan115Cookies.length ? transferForm.pan115Cookies.join("\n") : String(config.pan115Cookie || "");
}

function collectTransferConfig(): TransferConfig {
  const cookies = cookieLines(transferForm.pan115Cookie);
  return {
    ...transferForm,
    pan115Cookie: cookies.join("\n"),
    pan115Cookies: cookies,
    targetDirId: transferForm.targetDirId.trim() || "0",
    localPath115: transferForm.localPath115.trim(),
    pan115TargetCid: transferForm.pan115TargetCid.trim() || "0",
    pan123OauthApi: transferForm.pan123OauthApi.trim(),
    excludeSuffix: transferForm.excludeSuffix.trim(),
    excludeCid: transferForm.excludeCid.trim(),
    delete115AfterSuccess: transferForm.delete115AfterSuccess === true,
    concurrency: clampNumber(transferForm.concurrency, 1, 5, 5),
    pauseStartHour: clampNumber(transferForm.pauseStartHour, 0, 23, 18),
    pauseEndHour: clampNumber(transferForm.pauseEndHour, 0, 23, 1),
    downloadMinIntervalMs: clampNumber(transferForm.downloadMinIntervalMs, 0, 60000, 2500),
    downloadMaxAttempts: clampNumber(transferForm.downloadMaxAttempts, 1, 20, 5),
    downloadRetryBaseMs: clampNumber(transferForm.downloadRetryBaseMs, 1000, 120000, 8000),
    offlinePollMs: clampNumber(transferForm.offlinePollMs, 3000, 120000, 15000),
    offlineMaxPolls: clampNumber(transferForm.offlineMaxPolls, 1, 1000, 240),
    progressNotifyIntervalMs: clampNumber(transferForm.progressNotifyIntervalMs, 0, 600000, 60000),
  };
}

function cookieLines(value?: string) {
  return String(value || "")
    .split(/\n\s*\n|[\r\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, Math.round(number)));
}

async function loadTransfer() {
  transferLoading.value = true;
  try {
    const [config, list, cooldowns] = await Promise.all([
      transferApi.getConfig(),
      transferApi.tasks(),
      transferApi.accountCooldowns().catch(() => ({ accounts: [], cooldownMinutes: 30 })),
    ]);
    applyTransferConfig(config);
    transferTasks.value = list;
    coolingAccounts.value = cooldowns.accounts;
    syncSelectedTransferTask();
  } catch (error) {
    notifyError(`115 搬运加载失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    transferLoading.value = false;
  }
}

async function saveTransfer() {
  transferSaving.value = true;
  try {
    const data = await transferApi.putConfig(collectTransferConfig());
    applyTransferConfig(data.config);
    notifySuccess("115 搬运配置已保存");
    await loadTransferTasks();
    transferSettingsOpen.value = false;
  } catch (error) {
    notifyError(`保存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    transferSaving.value = false;
  }
}

async function loadTransferTasks() {
  try {
    transferTasks.value = await transferApi.tasks();
    syncSelectedTransferTask();
  } catch (error) {
    notifyError(`任务列表刷新失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function submitTransfer() {
  const text = submitText.value.trim();
  if (!text) {
    notifyError("请输入 115 分享链接和提取码");
    return;
  }
  submitting.value = true;
  try {
    const data = await transferApi.submit(text);
    submitText.value = "";
    notifySuccess(`已提交搬运：${data.tasks.length} 个任务`);
    await loadTransferTasks();
    createTransferDialog.value = false;
  } catch (error) {
    notifyError(`入队失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    submitting.value = false;
  }
}

async function submitLocalTransfer() {
  const path115 = transferForm.localPath115.trim();
  if (!path115) {
    notifyError("请输入 115 本地盘目录路径或 CID");
    return;
  }
  localSubmitting.value = true;
  try {
    const data = await transferApi.submitLocal(path115);
    notifySuccess(`已提交 115 本地盘搬运：${data.task.title || path115}`);
    await loadTransferTasks();
    createTransferDialog.value = false;
  } catch (error) {
    notifyError(`本地盘入队失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    localSubmitting.value = false;
  }
}

async function submit123to115Transfer() {
  // 默认从 115→123 的落地目录往回搬；输入框可自由指定其他源目录
  const sourceDirId = sourceDirId123.value.trim() || transferForm.targetDirId.trim() || "0";
  if (!/^\d+$/.test(sourceDirId)) {
    notifyError("123 源目录 ID 必须是数字（根目录填 0）");
    return;
  }
  transfer123to115Submitting.value = true;
  try {
    const data = await transferApi.submit123to115(sourceDirId);
    notifySuccess(`已提交 123→115 搬运：${data.task.title || sourceDirId}`);
    sourceDirId123.value = "";
    await loadTransferTasks();
    createTransferDialog.value = false;
  } catch (error) {
    notifyError(`123→115 入队失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    transfer123to115Submitting.value = false;
  }
}

async function kickQueue() {
  try {
    await transferApi.kick();
    notifySuccess("已触发队列处理");
    await loadTransferTasks();
  } catch (error) {
    notifyError(`触发失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function retryTask(task: TransferTask) {
  actingTaskId.value = task.id;
  try {
    await transferApi.requeue(task.id);
    notifySuccess("失败任务已重新排队");
    await loadTransferTasks();
  } catch (error) {
    notifyError(`重试失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    actingTaskId.value = "";
  }
}

async function deleteTask(task: TransferTask) {
  if (!(await confirm("确定删除这个搬运任务吗？", "删除搬运任务"))) return;
  actingTaskId.value = task.id;
  try {
    await transferApi.deleteTask(task.id);
    transferTasks.value = transferTasks.value.filter((item) => item.id !== task.id);
    if (selectedTransferTask.value?.id === task.id) {
      transferDetailOpen.value = false;
      selectedTransferTask.value = null;
    }
    notifySuccess("搬运任务已删除");
  } catch (error) {
    notifyError(`删除失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    actingTaskId.value = "";
  }
}

async function clearCompleted() {
  const done = transferTasks.value.filter((task) => task.status === "success");
  if (!done.length) {
    notifySuccess("没有完成任务可清理");
    return;
  }
  if (!(await confirm("清理已完成的搬运任务？", "清理完成任务"))) return;
  for (const task of done) {
    await transferApi.deleteTask(task.id);
  }
  notifySuccess(`已清理完成任务：${done.length} 个`);
  await loadTransferTasks();
}

async function loadCooldowns() {
  try {
    coolingAccounts.value = (await transferApi.accountCooldowns()).accounts;
  } catch {
    coolingAccounts.value = [];
  }
}

async function clearAccountCooldowns() {
  if (!coolingAccounts.value.length) {
    notifySuccess("当前没有账号被停用");
    return;
  }
  if (!(await confirm(`解除被停用 115 账号的冷却限制（${coolingNames.value.replace(/\n/g, "、")}）？`, "清除账号停用限制"))) return;
  try {
    const data = await transferApi.clearAccountCooldowns();
    notifySuccess(data.cleared ? `已解除停用：${data.accounts.join("、")}` : "当前没有账号被停用");
  } catch (error) {
    notifyError(`清除失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    await loadCooldowns();
  }
}

function toggleCompleted() {
  showCompleted.value = !showCompleted.value;
  localStorage.setItem("transferShowCompleted", showCompleted.value ? "1" : "0");
}

function openTransferTask(task: TransferTask) {
  selectedTransferTask.value = task;
  transferDetailOpen.value = true;
}

function syncSelectedTransferTask() {
  if (!selectedTransferTask.value) return;
  selectedTransferTask.value = transferTasks.value.find((task) => task.id === selectedTransferTask.value?.id) || null;
  if (!selectedTransferTask.value) transferDetailOpen.value = false;
}

async function showOfflineTasks() {
  offlinePanel.value = true;
  offlineLoading.value = true;
  try {
    const data = await transferApi.offline();
    offlineTasks.value = data.tasks || [];
  } catch (error) {
    notifyError(`离线进度加载失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    offlineLoading.value = false;
  }
}

async function deleteOfflineTask(task: TransferOfflineTask) {
  const id = task.id;
  if (!id || !(await confirm("删除这个 123 离线任务？", "删除离线任务"))) return;
  try {
    await transferApi.deleteOffline(id);
    notifySuccess("离线任务已删除");
    await showOfflineTasks();
  } catch (error) {
    notifyError(`删除离线任务失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

function transferStatusText(status?: string) {
  return {
    queued: "排队",
    running: "运行中",
    success: "完成",
    partial: "部分失败",
    failed: "失败",
    pending: "等待",
    skipped: "已存在",
  }[String(status || "")] || status || "--";
}

function offlineStatusText(status?: string | number | null) {
  return {
    running: "离线中",
    success: "完成",
    failed: "失败",
    queued: "排队",
  }[String(status || "")] || String(status || "--");
}

function taskTitle(task: TransferTask) {
  const prefix = task.kind === "pan123_share_copy" ? "123 分享转存 · " : task.kind === "pan123to115" ? "123→115 · " : "";
  return `${prefix}${task.title || task.shareCode || task.shareUrl || "115 分享"}`;
}

function taskSubtitle(task: TransferTask) {
  const shareUrl = task.shareUrl || "";
  if (task.kind === "pan123_share_copy") {
    const remote = task.remoteTaskId ? ` · 远端任务 ${task.remoteTaskId}` : "";
    return `${shareUrl} · 目标目录 ${task.targetDirId || "0"}${remote}`;
  }
  if (task.kind === "pan123to115") {
    const source = task.shareUrl || "";
    if (source && !source.startsWith("123://")) return `${source} → 115 目录 CID ${task.targetDirId || "0"}`;
    return `123 目录 ID ${task.sourceDirId || task.sourceText || "0"} → 115 目录 CID ${task.targetDirId || "0"}`;
  }
  if (shareUrl.startsWith("115://local")) return task.sourceText || shareUrl;
  return shareUrl;
}

function taskPercent(task: TransferTask) {
  const total = task.totalFiles || task.files?.length || 0;
  const done = task.doneFiles || 0;
  return total ? Math.round((done / total) * 100) : 0;
}

function fileDisplayName(file: TransferTaskFile) {
  const prefix = file.path?.length ? `${file.path.join("/")}/` : "";
  return `${prefix}${file.name || file.id}`;
}

function failedCount(task: TransferTask) {
  return (task.files || []).filter((file) => file.status === "failed").length;
}

function pendingCount(task: TransferTask) {
  return (task.files || []).filter((file) => file.status === "pending" || file.status === "running").length;
}

function canRetry(task: TransferTask) {
  return task.status === "failed" || task.status === "partial";
}

function transferStatusTone(status?: string): "success" | "warning" | "error" | "info" | "group" {
  const map: Record<string, "success" | "warning" | "error" | "info" | "group"> = {
    success: "success",
    failed: "error",
    partial: "warning",
    running: "group",
    queued: "info",
  };
  return map[String(status || "")] || "group";
}

// ============ Helper state (助手) ============
const helperForm = reactive({
  enabled: false,
  pan115Cookie: "",
  offlineTargetDirId: "0",
  trashPassword: "",
  dailyRecycleCleanupEnabled: false,
  dailyRecycleCleanupTime: "03:30",
  dailyRecycleCleanupTimeZone: "Asia/Shanghai",
  requestIntervalMs: 2500,
});

const helperTestText = ref("");
const helperStatusText = ref("未检查");
const helperRunning = ref(false);
const helperSaving = ref(false);
const helperStatus = ref<{ enabled: boolean; accountName?: string; userId?: string; message?: string } | null>(null);

function firstCookie(cookie?: string, legacyPool?: readonly string[]) {
  const direct = String(cookie || "").split(/\r?\n/).map((line) => line.trim()).find(Boolean);
  if (direct) return direct;
  return (legacyPool || []).map((line) => String(line || "").trim()).find(Boolean) || "";
}

function syncHelperFromConfig() {
  const helper = state.submissionConfig?.pan115Helper || {};
  helperForm.enabled = helper.enabled === true;
  helperForm.pan115Cookie = firstCookie(helper.pan115Cookie, helper.pan115Cookies);
  helperForm.offlineTargetDirId = helper.offlineTargetDirId || "0";
  helperForm.trashPassword = helper.trashPassword || "";
  helperForm.dailyRecycleCleanupEnabled = helper.dailyRecycleCleanupEnabled === true;
  helperForm.dailyRecycleCleanupTime = helper.dailyRecycleCleanupTime || "03:30";
  helperForm.dailyRecycleCleanupTimeZone = helper.dailyRecycleCleanupTimeZone || "Asia/Shanghai";
  helperForm.requestIntervalMs = Number(helper.requestIntervalMs || 2500);
}

async function saveHelper() {
  helperSaving.value = true;
  try {
    const next = ensureSubmissionConfig();
    const cookie = helperForm.pan115Cookie.trim();
    next.pan115Helper = {
      ...(next.pan115Helper || {}),
      enabled: helperForm.enabled,
      pan115Cookie: cookie,
      pan115Cookies: [],
      offlineTargetDirId: helperForm.offlineTargetDirId.trim() || "0",
      trashPassword: helperForm.trashPassword,
      dailyRecycleCleanupEnabled: helperForm.dailyRecycleCleanupEnabled,
      dailyRecycleCleanupTime: helperForm.dailyRecycleCleanupTime || "03:30",
      dailyRecycleCleanupTimeZone: helperForm.dailyRecycleCleanupTimeZone || "Asia/Shanghai",
      requestIntervalMs: Math.max(0, Number(helperForm.requestIntervalMs || 2500)),
    };
    await writeSubmissionConfig(next);
    notifySuccess("115 助手配置已保存");
  } catch (error) {
    notifyError(`保存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    helperSaving.value = false;
  }
}

async function refreshHelperStatus() {
  try {
    const data = await pan115HelperApi.status();
    helperStatus.value = data;
    helperStatusText.value = data.enabled
      ? `已启用：${data.accountName || "未知账号"} ${data.message || ""}`
      : data.message || "未启用";
  } catch (error) {
    helperStatusText.value = error instanceof Error ? error.message : String(error);
  }
}

async function runHelperAction(action: "offline" | "recycle") {
  helperRunning.value = true;
  try {
    let data: { success?: number; total?: number; failed?: number };
    if (action === "offline") {
      data = await pan115HelperApi.offline(helperTestText.value) as { success?: number; total?: number; failed?: number };
    } else {
      data = await pan115HelperApi.emptyRecycle() as { success?: number; total?: number; failed?: number };
    }
    notifySuccess(`执行完成：成功 ${data.success || 0}/${data.total || 0}`);
  } catch (error) {
    notifyError(`执行失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    helperRunning.value = false;
  }
}

// ============ Cookie state (扫码) ============
const cookieDevices = ref<Pan115Device[]>([]);
const cookieLoading = ref(false);
const cookiePolling = ref(false);
const qrcodeDataUrl = ref("");
const cookieSessionId = ref("");
const cookieStatusText = ref("未创建扫码会话");
const cookieText = ref("");
const cookieName = ref("");
const scanUrl = ref("");
const cookieExpiresAt = ref(0);
const cookieExpiresText = ref("未生成");
let countdownTimer: number | undefined;

const cookieForm = reactive({
  device: "alipaymini",
});

const canConfirmCookie = computed(() => !!cookieSessionId.value && !cookiePolling.value);

async function loadCookieDevices() {
  try {
    const data = await pan115CookieApi.devices();
    cookieDevices.value = data.devices || [];
    if (!cookieDevices.value.some((item) => item.id === cookieForm.device) && cookieDevices.value[0]) {
      cookieForm.device = cookieDevices.value[0].id;
    }
  } catch (error) {
    notifyError(`读取设备列表失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function createCookieSession() {
  cookieLoading.value = true;
  cookieText.value = "";
  qrcodeDataUrl.value = "";
  cookieSessionId.value = "";
  scanUrl.value = "";
  cookieExpiresAt.value = 0;
  cookieExpiresText.value = "未生成";
  try {
    const data = await pan115CookieApi.createSession(cookieForm.device);
    cookieSessionId.value = data.sessionId;
    qrcodeDataUrl.value = data.qrcodeDataUrl;
    scanUrl.value = data.scanUrl || "";
    cookieExpiresAt.value = parseExpireTime(data.expiresAt);
    cookieStatusText.value = "等待扫码";
    startCountdown();
    notifySuccess("二维码已生成");
    pollCookieStatus();
  } catch (error) {
    notifyError(`生成二维码失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    cookieLoading.value = false;
  }
}

async function pollCookieStatus() {
  if (!cookieSessionId.value || cookiePolling.value) return;
  cookiePolling.value = true;
  try {
    while (cookieSessionId.value) {
      const data = await pan115CookieApi.status(cookieSessionId.value);
      if (data.expiresAt) {
        cookieExpiresAt.value = parseExpireTime(data.expiresAt);
        updateExpireText();
      }
      const status = Number(data.status);
      if (status === 0) {
        cookieStatusText.value = "等待扫码";
      } else if (status === 1) {
        cookieStatusText.value = "已扫码，请在手机上确认登录";
      } else {
        cookieStatusText.value = data.statusText || String(data.status);
      }
      if (status === 2 || /登录成功|成功/.test(cookieStatusText.value)) {
        cookieStatusText.value = "登录成功，正在获取 Cookie";
        await confirmCookieSession();
        break;
      }
      if (status < 0) {
        stopCountdown();
        cookieSessionId.value = "";
        cookieExpiresText.value = data.statusText || "二维码失效";
        break;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1800));
    }
  } catch (error) {
    cookieStatusText.value = error instanceof Error ? error.message : String(error);
  } finally {
    cookiePolling.value = false;
  }
}

async function confirmCookieSession() {
  if (!cookieSessionId.value) return;
  try {
    const data = await pan115CookieApi.confirm(cookieSessionId.value);
    cookieText.value = data.cookieText || "";
    cookieSessionId.value = "";
    stopCountdown();
    cookieExpiresText.value = "已获取，可复制或保存";
    cookieStatusText.value = "登录成功，Cookie 已获取";
    notifySuccess("115 Cookie 已获取");
  } catch (error) {
    cookieStatusText.value = "获取 Cookie 失败，可确认手机已同意后重试";
    notifyError(`确认登录失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function copyCookie() {
  if (!cookieText.value) return;
  try {
    await navigator.clipboard.writeText(cookieText.value);
    notifySuccess("Cookie 已复制");
  } catch {
    notifyError("复制失败，请手动复制 Cookie 文本");
  }
}

function parseExpireTime(value: string | number | undefined): number {
  if (!value) return 0;
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function updateExpireText() {
  if (!cookieExpiresAt.value) {
    cookieExpiresText.value = "未生成";
    return;
  }
  const seconds = Math.max(0, Math.ceil((cookieExpiresAt.value - Date.now()) / 1000));
  if (seconds <= 0) {
    cookieExpiresText.value = "二维码已过期";
    cookieStatusText.value = cookieText.value ? cookieStatusText.value : "二维码已过期，请重新生成";
    cookieSessionId.value = "";
    stopCountdown();
    return;
  }
  cookieExpiresText.value = `二维码 ${seconds} 秒后过期`;
}

function startCountdown() {
  stopCountdown();
  updateExpireText();
  countdownTimer = window.setInterval(updateExpireText, 1000);
}

function stopCountdown() {
  if (countdownTimer) {
    window.clearInterval(countdownTimer);
    countdownTimer = undefined;
  }
}

function openScanUrl() {
  if (!scanUrl.value) {
    notifyError("当前设备没有返回可打开的扫码链接，请直接扫二维码");
    return;
  }
  window.open(scanUrl.value, "_blank", "noopener");
}

async function saveCookieToHelper() {
  const cookie = cookieText.value.trim();
  if (!cookie) return;
  try {
    await loadStatus();
    const config = ensureSubmissionConfig();
    const helper = config.pan115Helper || {};
    const namedCookie = cookieName.value.trim() ? `${cookieName.value.trim()}|${cookie}` : cookie;
    config.pan115Helper = {
      ...helper,
      enabled: helper.enabled ?? true,
      pan115Cookie: namedCookie,
      pan115Cookies: [],
    };
    await writeSubmissionConfig(config);
    syncHelperFromConfig();
    notifySuccess("已保存到 115 助手单账号 Cookie");
  } catch (error) {
    notifyError(`保存到 115 助手失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

// ============ Tab orchestration ============
function ensureTabLoaded(key: HubTab) {
  if (loadedTabs[key]) return;
  loadedTabs[key] = true;
  if (key === "transfer") {
    loadTransfer();
  } else if (key === "helper") {
    refreshHelperStatus();
    syncHelperFromConfig();
  } else if (key === "cookie") {
    loadCookieDevices();
  }
}

watch(tab, (next, prev) => {
  ensureTabLoaded(next);
  if (next !== "cookie") {
    stopCountdown();
    cookieSessionId.value = "";
  }
  if (prev !== next) {
    const targetPath = TAB_ROUTES[next];
    if (route.path !== targetPath && !syncingRoute) {
      syncingRoute = true;
      router.replace(targetPath).finally(() => { syncingRoute = false; });
    }
  }
});

watch(
  () => route.path,
  (path) => {
    const found = (Object.entries(TAB_ROUTES) as [HubTab, string][]).find(([, p]) => p === path);
    if (found && tab.value !== found[0] && !syncingRoute) {
      tab.value = found[0];
    }
  }
);

watch(() => state.loaded, (loaded) => {
  if (loaded && (tab.value === "helper" || loadedTabs.helper)) {
    syncHelperFromConfig();
  }
}, { immediate: true });

const tabsList = [
  { key: "transfer", label: "搬运", icon: "mdi-cloud-sync" },
  { key: "helper", label: "助手", icon: "mdi-tools" },
  { key: "cookie", label: "扫码", icon: "mdi-cookie" },
];

onMounted(() => {
  tab.value = props.initialTab;
  ensureTabLoaded(props.initialTab);
});

watch(
  () => props.initialTab,
  (newTab, oldTab) => {
    if (newTab !== oldTab && tab.value !== newTab) {
      tab.value = newTab;
      ensureTabLoaded(newTab);
    }
  }
);

onUnmounted(() => {
  stopCountdown();
});
</script>

<template>
  <div class="page fade-rise" data-group="pan115">
    <PageHero
      group="pan115"
      icon="mdi-cloud-key-outline"
      title="115 中心"
      desc="115 搬运队列、单账号助手与扫码登录统一管理。"
    >
      <template #status>
        <span class="chip-status" :data-tone="helperStatus?.enabled ? 'success' : 'neutral'">
          <v-icon size="14">{{ helperStatus?.enabled ? 'mdi-check-circle' : 'mdi-circle-outline' }}</v-icon>
          助手{{ helperStatus?.enabled ? '已启用' : '未启用' }}
        </span>
        <span class="chip-status" data-tone="info">
          <v-icon size="14">mdi-format-list-checks</v-icon>
          搬运 {{ taskStats.total }}
        </span>
        <span v-if="coolingAccounts.length" class="chip-status" data-tone="warning" :title="coolingNames">
          <v-icon size="14">mdi-snowflake</v-icon>
          {{ coolingText }}
        </span>
      </template>
    </PageHero>

    <SegmentedTabs v-model="tab" :tabs="tabsList" />

    <!-- ====================== Transfer Tab ====================== -->
    <div v-show="tab === 'transfer'" class="section-stack">
      <div class="stat-grid">
        <StatTile label="搬运任务总数" :value="taskStats.total" icon="mdi-cloud-sync" tone="group" />
        <StatTile label="运行中" :value="taskStats.running" icon="mdi-progress-clock" tone="info" />
        <StatTile label="排队" :value="taskStats.queued" icon="mdi-clock-outline" tone="info" />
        <StatTile label="异常" :value="taskStats.failed" icon="mdi-alert-circle-outline" tone="error" />
      </div>

      <div class="hub-workspace-toolbar">
        <div class="hub-workspace-copy">
          <strong>搬运工作区</strong>
          <span>{{ transferForm.enabled ? "队列已启用" : "队列未启用" }} · 默认{{ showCompleted ? "显示" : "隐藏" }}已完成任务</span>
        </div>
        <div class="hub-workspace-actions">
          <v-btn color="primary" prepend-icon="mdi-plus" @click="createTransferDialog = true">新建任务</v-btn>
          <v-btn variant="outlined" prepend-icon="mdi-cog-outline" @click="transferSettingsOpen = true">搬运设置</v-btn>
          <v-btn variant="text" icon="mdi-refresh" :loading="transferLoading" aria-label="刷新搬运任务" title="刷新搬运任务" @click="loadTransfer" />
          <v-menu location="bottom end">
            <template #activator="{ props: menuProps }">
              <v-btn v-bind="menuProps" variant="text" icon="mdi-dots-horizontal" aria-label="更多搬运操作" title="更多搬运操作" />
            </template>
            <v-list density="compact">
              <v-list-item prepend-icon="mdi-play" title="触发队列" @click="kickQueue" />
              <v-list-item prepend-icon="mdi-progress-download" title="查看离线进度" @click="showOfflineTasks" />
              <v-list-item :prepend-icon="showCompleted ? 'mdi-eye-off-outline' : 'mdi-eye-outline'" :title="showCompleted ? '隐藏完成任务' : '显示完成任务'" @click="toggleCompleted" />
              <v-list-item prepend-icon="mdi-broom" title="清理完成任务" @click="clearCompleted" />
              <v-list-item prepend-icon="mdi-snowflake-off" title="清除账号停用限制" @click="clearAccountCooldowns" />
            </v-list>
          </v-menu>
        </div>
      </div>

      <GlassCard
        accent="group"
        icon="mdi-format-list-bulleted"
        title="搬运任务"
        desc="以任务为主工作区；选择一行查看完整文件、进度和错误信息。"
      >
        <template #actions>
          <span class="chip-status" data-tone="info">{{ visibleTasks.length }} 条</span>
        </template>

        <div v-if="!transferTasks.length" class="empty-state">
          <v-icon size="40">mdi-inbox-outline</v-icon>
          <p>暂无搬运任务</p>
          <v-btn color="primary" prepend-icon="mdi-plus" @click="createTransferDialog = true">新建搬运任务</v-btn>
        </div>
        <div v-else-if="!visibleTasks.length" class="empty-state">
          <v-icon size="40">mdi-check-circle-outline</v-icon>
          <p>已完成任务当前处于隐藏状态。</p>
          <v-btn variant="outlined" @click="toggleCompleted">显示完成任务</v-btn>
        </div>

        <div v-else class="hub-task-table" role="table" aria-label="115 搬运任务">
          <div class="hub-task-table-head" role="row">
            <span>任务</span>
            <span>状态</span>
            <span>进度</span>
            <span>更新时间</span>
            <span aria-label="操作"></span>
          </div>
          <div
            v-for="task in visibleTasks"
            :key="task.id"
            class="hub-task-table-row"
            role="row"
            tabindex="0"
            @click="openTransferTask(task)"
            @keydown.enter="openTransferTask(task)"
            @keydown.space.prevent="openTransferTask(task)"
          >
            <div class="hub-task-primary" role="cell">
              <strong :title="taskTitle(task)">{{ taskTitle(task) }}</strong>
              <span>{{ taskSubtitle(task) || `${task.totalFiles || task.files?.length || 0} 个文件` }}</span>
            </div>
            <div role="cell" data-label="状态">
              <span class="chip-status" :data-tone="transferStatusTone(task.status)">{{ transferStatusText(task.status) }}</span>
            </div>
            <div class="hub-task-progress-cell" role="cell" data-label="进度">
              <div>
                <span>{{ task.doneFiles || 0 }}/{{ task.totalFiles || task.files?.length || 0 }}</span>
                <span>{{ taskPercent(task) }}%</span>
              </div>
              <v-progress-linear :model-value="taskPercent(task)" height="6" rounded color="primary" />
            </div>
            <span class="hub-task-updated" role="cell" data-label="更新时间">{{ task.updatedAt || "--" }}</span>
            <div class="hub-task-row-actions" role="cell" @click.stop>
              <v-btn
                v-if="canRetry(task)"
                icon="mdi-reload"
                size="small"
                variant="text"
                :loading="actingTaskId === task.id"
                aria-label="重试失败任务"
                title="重试失败任务"
                @click="retryTask(task)"
              />
              <v-btn icon="mdi-trash-can-outline" size="small" variant="text" aria-label="删除任务" title="删除任务" :loading="actingTaskId === task.id" @click="deleteTask(task)" />
              <v-icon icon="mdi-chevron-right" size="17" />
            </div>
          </div>
        </div>
      </GlassCard>

      <v-dialog v-model="createTransferDialog" :fullscreen="isMobile" max-width="720">
        <v-card class="hub-overlay-card" elevation="0">
          <header class="hub-overlay-head">
            <div>
              <span class="hub-overlay-icon"><v-icon icon="mdi-cloud-upload-outline" size="21" /></span>
              <div>
                <strong>新建搬运任务</strong>
                <span>提交 115 分享链接，或从 115 本地盘路径/CID 创建任务。</span>
              </div>
            </div>
            <v-btn icon="mdi-close" variant="text" size="small" aria-label="关闭新建任务" @click="createTransferDialog = false" />
          </header>
          <v-card-text class="hub-dialog-body">
            <section class="hub-form-section">
              <div class="hub-form-section-title">
                <v-icon icon="mdi-link-variant" size="18" />
                <div><strong>115 分享</strong><span>支持一段文本中包含多条分享和提取码。</span></div>
              </div>
              <v-textarea
                v-model="submitText"
                label="115 分享文本"
                placeholder="https://115cdn.com/s/xxxx?password=yyyy 或文本中带提取码"
                :rows="7"
                variant="outlined"
                density="compact"
              />
              <div class="hub-form-actions">
                <v-btn color="primary" :loading="submitting" @click="submitTransfer">提交分享搬运</v-btn>
              </div>
            </section>

            <section class="hub-form-section">
              <div class="hub-form-section-title">
                <v-icon icon="mdi-folder-arrow-right-outline" size="18" />
                <div><strong>115 本地盘</strong><span>输入目录路径或 CID，使用当前搬运配置入队。</span></div>
              </div>
              <v-text-field
                v-model="transferForm.localPath115"
                label="115 本地盘目录路径 / CID"
                placeholder="cid:123456 或 /云下载/待搬运"
                variant="outlined"
                density="compact"
              />
              <div class="hub-form-actions">
                <v-btn variant="outlined" :loading="localSubmitting" @click="submitLocalTransfer">提交本地盘搬运</v-btn>
              </div>
            </section>

            <section class="hub-form-section">
              <div class="hub-form-section-title">
                <v-icon icon="mdi-swap-horizontal" size="18" />
                <div><strong>123 → 115</strong><span>把 123 网盘目录搬到 115 助手账号：能秒传的秒传，其余走 115 离线下载。</span></div>
              </div>
              <v-text-field
                v-model="sourceDirId123"
                label="123 源目录 ID"
                :placeholder="`留空默认用 115→123 落地目录（${transferForm.targetDirId || '0'}），可填其他目录 ID`"
                variant="outlined"
                density="compact"
              />
              <div class="hub-status-line">115 目标目录 CID：{{ transferForm.pan115TargetCid || "0" }}（在搬运设置中修改）· 目标账号为 115 助手 Cookie</div>
              <div class="hub-form-actions">
                <v-btn color="primary" :loading="transfer123to115Submitting" @click="submit123to115Transfer">提交 123→115 搬运</v-btn>
              </div>
            </section>
          </v-card-text>
        </v-card>
      </v-dialog>

      <v-dialog
        v-if="transferSettingsOpen"
        v-model="transferSettingsOpen"
        :fullscreen="isMobile"
        max-width="680"
        class="side-drawer-dialog"
      >
        <v-card class="hub-drawer" elevation="0">
          <div class="hub-drawer-shell">
          <header class="hub-overlay-head">
            <div>
              <span class="hub-overlay-icon"><v-icon icon="mdi-cog-outline" size="21" /></span>
              <div><strong>搬运设置</strong><span>Cookie、目标目录、并发、暂停窗口与轮询参数。</span></div>
            </div>
            <v-btn icon="mdi-close" variant="text" size="small" aria-label="关闭搬运设置" @click="transferSettingsOpen = false" />
          </header>
          <div class="hub-drawer-body">
            <section class="hub-form-section">
              <div class="hub-form-section-title">
                <v-icon icon="mdi-tune-variant" size="18" />
                <div><strong>基础配置</strong><span>保留原有搬运开关、凭据、目录和过滤字段。</span></div>
              </div>
              <FormGrid>
                <v-switch v-model="transferForm.enabled" label="启用 115 搬运" color="primary" hide-details />
                <v-text-field v-model="transferForm.targetDirId" label="123 目标目录 ID" variant="outlined" density="compact" />
                <v-text-field v-model="transferForm.localPath115" label="115 本地盘目录路径 / CID" variant="outlined" density="compact" />
                <v-text-field v-model="transferForm.pan115TargetCid" label="115 目标目录 CID（123→115）" variant="outlined" density="compact" />
                <v-text-field v-model="transferForm.pan123OauthApi" label="123 授权服务地址（可选，默认社区官方）" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.concurrency" label="并发任务数 1-5" type="number" variant="outlined" density="compact" />
                <v-text-field v-model="transferForm.excludeSuffix" label="排除后缀" variant="outlined" density="compact" />
                <v-text-field v-model="transferForm.excludeCid" label="排除 115 目录 CID" variant="outlined" density="compact" />
                <v-switch v-model="transferForm.delete115AfterSuccess" label="成功后删除 115 源文件" color="warning" hide-details />
                <v-switch v-model="transferForm.pauseEnabled" label="启用晚高峰暂停" color="primary" hide-details />
                <v-select v-model="transferForm.pauseTimeZone" :items="timeZones" label="暂停时区" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.pauseStartHour" label="暂停开始小时" type="number" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.pauseEndHour" label="暂停结束小时" type="number" variant="outlined" density="compact" />
              </FormGrid>
              <v-textarea
                v-model="transferForm.pan115Cookie"
                label="115 Cookie 池"
                placeholder="每行一个 Cookie；可写：账号名|UID=...; CID=...; SEID=..."
                :rows="6"
                variant="outlined"
                density="compact"
              />
            </section>

            <section class="hub-form-section">
              <div class="hub-form-section-title">
                <v-icon icon="mdi-timer-cog-outline" size="18" />
                <div><strong>请求与轮询</strong><span>115 取直链重试、123 离线轮询与进度通知。</span></div>
              </div>
              <FormGrid>
                <v-text-field v-model.number="transferForm.downloadMinIntervalMs" label="取直链最小间隔 ms" type="number" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.downloadMaxAttempts" label="取直链最大重试" type="number" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.downloadRetryBaseMs" label="取直链退避基准 ms" type="number" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.offlinePollMs" label="离线轮询间隔 ms" type="number" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.offlineMaxPolls" label="离线最大轮询次数" type="number" variant="outlined" density="compact" />
                <v-text-field v-model.number="transferForm.progressNotifyIntervalMs" label="进度通知间隔 ms" type="number" variant="outlined" density="compact" />
              </FormGrid>
            </section>

          </div>
          <footer class="hub-drawer-actions">
            <v-btn variant="text" :loading="transferLoading" @click="loadTransfer">重新读取</v-btn>
            <v-btn color="primary" :loading="transferSaving" @click="saveTransfer">保存配置</v-btn>
          </footer>
          </div>
        </v-card>
      </v-dialog>

      <v-dialog
        v-if="selectedTransferTask"
        v-model="transferDetailOpen"
        :fullscreen="isMobile"
        max-width="680"
        class="side-drawer-dialog"
      >
        <v-card class="hub-drawer" elevation="0">
          <div class="hub-drawer-shell">
          <header class="hub-overlay-head">
            <div>
              <span class="hub-overlay-icon"><v-icon icon="mdi-cloud-sync-outline" size="21" /></span>
              <div>
                <strong>{{ taskTitle(selectedTransferTask) }}</strong>
                <span>{{ taskSubtitle(selectedTransferTask) || selectedTransferTask.id }}</span>
              </div>
            </div>
            <v-btn icon="mdi-close" variant="text" size="small" aria-label="关闭任务详情" @click="transferDetailOpen = false" />
          </header>
          <div class="hub-drawer-body">
            <div class="hub-detail-summary">
              <div><span>状态</span><strong><span class="chip-status" :data-tone="transferStatusTone(selectedTransferTask.status)">{{ transferStatusText(selectedTransferTask.status) }}</span></strong></div>
              <div><span>完成</span><strong>{{ selectedTransferTask.doneFiles || 0 }}/{{ selectedTransferTask.totalFiles || selectedTransferTask.files?.length || 0 }}</strong></div>
              <div><span>等待</span><strong>{{ pendingCount(selectedTransferTask) }}</strong></div>
              <div><span>失败</span><strong>{{ failedCount(selectedTransferTask) }}</strong></div>
            </div>

            <section class="hub-form-section">
              <div class="hub-form-section-title">
                <v-icon icon="mdi-progress-clock" size="18" />
                <div><strong>总体进度</strong><span>{{ taskPercent(selectedTransferTask) }}% · {{ selectedTransferTask.updatedAt || "暂无更新时间" }}</span></div>
              </div>
              <v-progress-linear :model-value="taskPercent(selectedTransferTask)" height="9" rounded color="primary" />
              <v-alert v-if="selectedTransferTask.error" type="error" variant="tonal" density="compact" class="mt-2">
                {{ selectedTransferTask.error }}
              </v-alert>
            </section>

            <section class="hub-form-section">
              <div class="hub-form-section-title">
                <v-icon icon="mdi-file-multiple-outline" size="18" />
                <div><strong>文件明细</strong><span>{{ selectedTransferTask.files?.length || 0 }} 个文件</span></div>
              </div>
              <div v-if="!selectedTransferTask.files?.length" class="empty-state compact">
                <v-icon size="30">mdi-file-hidden</v-icon>
                <p>当前任务没有文件明细。</p>
              </div>
              <div v-else class="hub-file-table hub-file-table--detail">
                <div v-for="file in selectedTransferTask.files" :key="`${selectedTransferTask.id}-${file.id}`" class="hub-file-row">
                  <strong :title="fileDisplayName(file)">{{ fileDisplayName(file) }}</strong>
                  <span>{{ file.method || transferStatusText(file.status) }}</span>
                  <span>{{ file.offlineStatusText || offlineStatusText(file.offlineStatus) }}</span>
                  <span>{{ file.offlineProgress != null ? `${file.offlineProgress}%` : "--" }}</span>
                  <span>{{ formatBytes(file.size) || "--" }}</span>
                </div>
              </div>
            </section>
          </div>
          <footer class="hub-drawer-actions">
            <v-btn variant="text" color="error" prepend-icon="mdi-trash-can-outline" :loading="actingTaskId === selectedTransferTask.id" @click="deleteTask(selectedTransferTask)">删除任务</v-btn>
            <div class="hub-drawer-action-group">
              <v-btn v-if="canRetry(selectedTransferTask)" variant="outlined" prepend-icon="mdi-reload" :loading="actingTaskId === selectedTransferTask.id" @click="retryTask(selectedTransferTask)">重试失败</v-btn>
              <v-btn color="primary" @click="transferDetailOpen = false">完成</v-btn>
            </div>
          </footer>
          </div>
        </v-card>
      </v-dialog>

      <v-dialog v-model="offlinePanel" max-width="920">
        <v-card>
          <v-card-title class="hub-dialog-title">
            <span>123 离线进度</span>
            <div class="hub-dialog-actions">
              <v-btn size="small" variant="text" :loading="offlineLoading" @click="showOfflineTasks">刷新</v-btn>
              <v-btn size="small" variant="text" @click="offlinePanel = false">关闭</v-btn>
            </div>
          </v-card-title>
          <v-card-text>
            <div v-if="!offlineTasks.length" class="empty-state">
              <v-icon size="40">mdi-inbox-outline</v-icon>
              <p>暂无 123 离线任务或当前 OpenAPI 不支持列出离线任务。</p>
            </div>
            <div v-else class="hub-offline-list">
              <div v-for="task in offlineTasks" :key="String(task.id)" class="hub-offline-row">
                <strong :title="task.name">{{ task.name || `离线任务 #${task.id}` }}</strong>
                <span class="chip-status" data-tone="info">{{ task.statusText || offlineStatusText(task.status) }}</span>
                <span class="mono-value mono-value-sm">{{ task.progress != null ? `${task.progress}%` : "--" }}</span>
                <span class="mono-value mono-value-sm">{{ formatBytes(task.size) || "--" }}</span>
                <v-btn size="small" variant="outlined" @click="deleteOfflineTask(task)">删除</v-btn>
                <div v-if="task.message" class="hub-offline-message">{{ task.message }}</div>
              </div>
            </div>
          </v-card-text>
        </v-card>
      </v-dialog>
    </div>

    <!-- ====================== Helper Tab ====================== -->
    <div v-show="tab === 'helper'" class="section-grid">
      <GlassCard
        accent="group"
        icon="mdi-tools"
        title="115 助手"
        desc="单账号提交 115 离线磁力 / ed2k，并支持定时清理回收站。"
      >
        <template #actions>
          <v-btn variant="text" @click="refreshHelperStatus">检查状态</v-btn>
          <v-btn color="primary" :loading="helperSaving" @click="saveHelper">保存配置</v-btn>
        </template>

        <FormGrid>
          <v-switch v-model="helperForm.enabled" label="启用 115 助手" hide-details />
          <v-text-field v-model="helperForm.offlineTargetDirId" label="离线保存目录 ID" variant="outlined" density="compact" />
          <v-text-field v-model.number="helperForm.requestIntervalMs" label="请求间隔毫秒" type="number" variant="outlined" density="compact" />
          <v-text-field v-model="helperForm.trashPassword" label="回收站密码" type="password" variant="outlined" density="compact" />
          <v-switch v-model="helperForm.dailyRecycleCleanupEnabled" label="每日自动清理回收站" hide-details />
          <v-text-field v-model="helperForm.dailyRecycleCleanupTime" label="每日清理时间" type="time" variant="outlined" density="compact" />
          <v-select v-model="helperForm.dailyRecycleCleanupTimeZone" :items="timeZones" label="清理时区" variant="outlined" density="compact" />
        </FormGrid>

        <v-textarea
          v-model="helperForm.pan115Cookie"
          label="115 Cookie"
          placeholder="可直接粘贴 Cookie；也可写：账号名|UID=...; CID=...; SEID=..."
          :rows="5"
          variant="outlined"
          density="compact"
          class="hub-textarea"
        />
        <div class="hub-status-line">状态：{{ helperStatusText }}</div>
      </GlassCard>

      <GlassCard
        accent="info"
        icon="mdi-flask-outline"
        title="助手测试"
        desc="直接提交 magnet / ed2k 离线任务，或手动清空回收站。"
      >
        <v-textarea
          v-model="helperTestText"
          label="测试文本"
          placeholder="粘贴 magnet 或 ed2k 链接，支持多条"
          :rows="8"
          variant="outlined"
          density="compact"
        />
        <div class="button-row">
          <v-btn :loading="helperRunning" @click="runHelperAction('offline')">提交离线</v-btn>
          <v-btn color="error" variant="outlined" :loading="helperRunning" @click="runHelperAction('recycle')">清空回收站</v-btn>
        </div>
      </GlassCard>
    </div>

    <!-- ====================== Cookie Tab ====================== -->
    <div v-show="tab === 'cookie'" class="section-stack">
      <GlassCard
        accent="group"
        icon="mdi-cookie"
        title="115 扫码登录"
        desc="扫码获取 115 Cookie，可复制后填入 115 助手配置。"
      >
        <template #actions>
          <v-btn color="primary" :loading="cookieLoading" @click="createCookieSession">生成二维码</v-btn>
          <v-btn variant="outlined" :disabled="!scanUrl" @click="openScanUrl">打开扫码链接</v-btn>
          <v-btn variant="outlined" :disabled="!canConfirmCookie" @click="confirmCookieSession">获取 Cookie</v-btn>
        </template>

        <div class="cookie-grid">
          <div class="cookie-form-side">
            <v-select
              v-model="cookieForm.device"
              :items="cookieDevices"
              item-title="label"
              item-value="id"
              label="扫码设备"
              variant="outlined"
              density="compact"
            />
            <div class="hub-status-line">状态：{{ cookieStatusText }}</div>
            <div class="hub-status-line">有效期：{{ cookieExpiresText }}</div>
          </div>

          <div class="qr-box">
            <img v-if="qrcodeDataUrl" :src="qrcodeDataUrl" alt="115 登录二维码" />
            <div v-else class="qr-empty">
              <v-icon size="36">mdi-qrcode-scan</v-icon>
              <span>点击生成二维码</span>
            </div>
          </div>
        </div>

        <v-textarea
          v-model="cookieText"
          label="Cookie"
          :rows="5"
          readonly
          variant="outlined"
          density="compact"
          class="hub-textarea"
        />
        <div class="cookie-actions">
          <v-text-field
            v-model="cookieName"
            label="账号备注"
            placeholder="可选，例如：主账号"
            variant="outlined"
            density="compact"
            hide-details
          />
          <v-btn variant="outlined" :disabled="!cookieText" @click="saveCookieToHelper">保存到 115 助手</v-btn>
          <v-btn variant="outlined" :disabled="!cookieText" @click="copyCookie">复制 Cookie</v-btn>
        </div>
      </GlassCard>
    </div>
  </div>
</template>

<style scoped>
.hub-textarea {
  margin-top: 12px;
}

.button-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.local-row {
  margin-top: 10px;
}

.mt-2 {
  margin-top: 6px;
}

/* ----- Transfer workspace ----- */
.hub-workspace-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border: 1px solid var(--glass-border-2);
  border-radius: var(--radius-surface);
  background: var(--surface-subtle);
  box-shadow: inset 0 1px 0 var(--glass-highlight);
  backdrop-filter: var(--surface-filter);
  -webkit-backdrop-filter: var(--surface-filter);
}

.hub-workspace-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.hub-workspace-copy strong {
  font-size: 14px;
  color: var(--text-primary);
}

.hub-workspace-copy span {
  font-size: 11.5px;
  color: var(--text-muted);
}

.hub-workspace-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.hub-task-table {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
}

.hub-task-table-head,
.hub-task-table-row {
  display: grid;
  grid-template-columns: minmax(240px, 1.7fr) 96px minmax(150px, 0.8fr) 150px 112px;
  gap: 14px;
  align-items: center;
}

.hub-task-table-head {
  min-height: 40px;
  padding: 0 14px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-subtle);
  color: var(--text-muted);
  font-size: 10.5px;
  font-weight: 700;
}

.hub-task-table-row {
  min-width: 0;
  min-height: 64px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: transparent;
  cursor: pointer;
  transition: background-color var(--transition), color var(--transition);
}

.hub-task-table-row:last-child {
  border-bottom: 0;
}

.hub-task-table-row:hover,
.hub-task-table-row:focus-visible {
  outline: none;
  background: var(--bg-hover);
}

.hub-task-primary {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.hub-task-primary strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12.5px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hub-task-primary span,
.hub-task-updated {
  overflow: hidden;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hub-task-progress-cell {
  min-width: 0;
}

.hub-task-progress-cell > div {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 5px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 10.5px;
}

.hub-task-row-actions,
.hub-drawer-action-group {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  color: var(--text-muted);
}

.hub-overlay-card {
  overflow: hidden !important;
}

.hub-overlay-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 16px;
  border-bottom: 1px solid var(--border);
}

.hub-overlay-head > div:first-child {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
}

.hub-overlay-head > div:first-child > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.hub-overlay-head strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hub-overlay-head > div:first-child > div > span {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hub-overlay-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: var(--radius-control);
  background: rgba(var(--v-theme-primary), 0.16);
  color: rgb(var(--v-theme-primary));
}

.hub-dialog-body,
.hub-drawer-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  overflow-y: auto;
  padding: 16px !important;
}

.hub-dialog-body {
  max-height: min(74dvh, 720px);
}

.hub-drawer {
  height: 100%;
  border-left: 1px solid var(--overlay-border) !important;
  background: var(--overlay-surface) !important;
  color: var(--text-primary) !important;
  box-shadow: var(--shadow-overlay) !important;
  backdrop-filter: var(--overlay-filter);
  -webkit-backdrop-filter: var(--overlay-filter);
}

.hub-drawer-shell {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  height: 100%;
  min-width: 0;
}

.hub-drawer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 16px max(12px, env(safe-area-inset-bottom));
  border-top: 1px solid var(--border);
  background: var(--surface-strong);
}

.hub-form-section {
  flex: 0 0 auto;
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
}

.hub-form-section-title {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  margin-bottom: 12px;
  color: rgb(var(--v-theme-primary));
}

.hub-form-section-title > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.hub-form-section-title strong {
  color: var(--text-primary);
  font-size: 12.5px;
}

.hub-form-section-title span {
  color: var(--text-muted);
  font-size: 10.5px;
}

.hub-form-actions {
  display: flex;
  justify-content: flex-end;
}

.hub-detail-summary {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  overflow: hidden;
}

.hub-detail-summary > div {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: 12px;
  border-right: 1px solid var(--border);
}

.hub-detail-summary > div:last-child {
  border-right: 0;
}

.hub-detail-summary span:first-child {
  color: var(--text-muted);
  font-size: 10.5px;
}

.hub-detail-summary strong {
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 13px;
}

.hub-file-table {
  display: grid;
  gap: 4px;
}

.hub-file-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 100px 120px 72px 84px;
  gap: 8px;
  align-items: center;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--glass-bg-2);
  font-size: 11.5px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.hub-file-row strong {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-sans);
}

.hub-dialog-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.hub-dialog-actions {
  display: flex;
  gap: 6px;
}

.hub-offline-list {
  display: grid;
  gap: 6px;
}

.hub-offline-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 100px 72px 90px 72px;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--glass-border-3);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 11.5px;
  background: var(--glass-bg-2);
}

.hub-offline-row strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.hub-offline-message {
  grid-column: 1 / -1;
  color: var(--error);
  font-size: 11.5px;
}

/* ----- Helper ----- */
.hub-status-line {
  font-size: 11.5px;
  margin-top: 8px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--glass-bg-3);
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

/* ----- Cookie ----- */
.cookie-grid {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) 220px;
  gap: 16px;
  align-items: stretch;
}

.cookie-form-side {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.qr-box {
  width: 220px;
  height: 220px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-bg-2);
  overflow: hidden;
}

.qr-box img {
  max-width: 200px;
  max-height: 200px;
  border-radius: var(--radius-sm);
}

.qr-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 12px;
}

.cookie-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  align-items: center;
}

.cookie-actions .v-input {
  flex: 1 1 200px;
}

/* ----- chip-status neutral fallback ----- */
.chip-status[data-tone="neutral"] {
  background: var(--glass-bg-3);
  color: var(--text-muted);
  border-color: var(--glass-border-3);
}

/* ----- Responsive ----- */
@media (max-width: 960px) {
  .hub-task-table-head,
  .hub-task-table-row {
    grid-template-columns: minmax(180px, 1fr) 88px minmax(130px, 0.7fr) 88px;
  }

  .hub-task-table-head > :nth-child(4),
  .hub-task-updated {
    display: none;
  }

  .hub-file-row,
  .hub-offline-row {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .hub-dialog-title {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (max-width: 640px) {
  .hub-workspace-toolbar,
  .hub-overlay-head,
  .hub-drawer-actions {
    align-items: stretch;
  }

  .hub-workspace-toolbar {
    flex-direction: column;
  }

  .hub-workspace-actions {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 40px 40px;
  }

  .hub-workspace-actions .v-btn {
    min-width: 0;
  }

  .hub-task-table-head {
    display: none;
  }

  .hub-task-table-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    padding: 12px;
  }

  .hub-task-primary,
  .hub-task-progress-cell {
    grid-column: 1 / -1;
  }

  .hub-task-row-actions {
    align-self: center;
    grid-column: 2;
    grid-row: 2;
  }

  .hub-overlay-head {
    padding-top: max(14px, env(safe-area-inset-top));
  }

  .hub-overlay-head > div:first-child > div > span {
    white-space: normal;
  }

  .hub-dialog-body {
    max-height: none;
  }

  .hub-drawer-actions {
    flex-direction: column-reverse;
  }

  .hub-drawer-actions > .v-btn,
  .hub-drawer-action-group,
  .hub-drawer-action-group .v-btn {
    width: 100%;
  }

  .hub-drawer-action-group {
    flex-direction: column-reverse;
  }

  .hub-detail-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hub-detail-summary > div:nth-child(2) {
    border-right: 0;
  }

  .hub-detail-summary > div:nth-child(-n + 2) {
    border-bottom: 1px solid var(--border);
  }

  .hub-file-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 5px 10px;
  }

  .hub-file-row strong {
    grid-column: 1 / -1;
    white-space: normal;
  }

  .cookie-grid {
    grid-template-columns: 1fr;
  }

  .qr-box {
    width: 100%;
    max-width: 240px;
    height: 240px;
    margin: 0 auto;
  }
}
</style>
