import { reactive, readonly } from "vue";
import { adminApi, submissionApi } from "@/api";
import type { AdminStatus, SubmissionConfig, SubmissionStatus } from "@/api/types";

interface GlobalState {
  loaded: boolean;
  loading: boolean;
  status: AdminStatus | null;
  submissionConfig: SubmissionConfig | null;
  submissionStatus: SubmissionStatus | null;
  toast: { kind: "info" | "success" | "error" | "warn"; message: string } | null;
}

interface ConfirmationState {
  open: boolean;
  title: string;
  message: string;
}

const state = reactive<GlobalState>({
  loaded: false,
  loading: false,
  status: null,
  submissionConfig: null,
  submissionStatus: null,
  toast: null,
});

const confirmation = reactive<ConfirmationState>({
  open: false,
  title: "请确认",
  message: "",
});

let confirmationResolver: ((value: boolean) => void) | null = null;

let toastTimer: number | undefined;
let submissionConfigRevision = 0;

export function setToast(message: string, kind: "info" | "success" | "error" | "warn" = "info") {
  state.toast = { message, kind };
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    state.toast = null;
  }, 4200);
}

export function notifySuccess(message: string) {
  setToast(message, "success");
}

export function notifyError(message: string) {
  setToast(message, "error");
}

export async function confirm(message: string, title = "请确认"): Promise<boolean> {
  if (confirmationResolver) confirmationResolver(false);
  confirmation.title = title;
  confirmation.message = message;
  confirmation.open = true;
  return new Promise<boolean>((resolve) => {
    confirmationResolver = resolve;
  });
}

export function resolveConfirmation(value: boolean) {
  confirmation.open = false;
  const resolver = confirmationResolver;
  confirmationResolver = null;
  resolver?.(value);
}

export function ensureSubmissionConfig(): SubmissionConfig {
  const config: SubmissionConfig = state.submissionConfig || {};
  config.system = config.system || {};
  config.templates = config.templates || {};
  config.routing = config.routing || {};
  config.pan115Helper = config.pan115Helper || {};
  config.ruleConfig = config.ruleConfig || {};
  config.ruleConfig.recognition = config.ruleConfig.recognition || {};
  config.ruleConfig.display = config.ruleConfig.display || {};
  config.ruleConfig.display.sourceLabels = config.ruleConfig.display.sourceLabels || [];
  state.submissionConfig = config;
  return config;
}

export async function writeSubmissionConfig(config: SubmissionConfig): Promise<void> {
  const revision = ++submissionConfigRevision;
  const data = await submissionApi.putConfig(config);
  const verified = await submissionApi.getConfig(true);
  const savedAt = Date.parse(data.config.updatedAt || "");
  const verifiedAt = Date.parse(verified.config.updatedAt || "");
  if (
    !Number.isFinite(savedAt)
    || !Number.isFinite(verifiedAt)
    || verifiedAt < savedAt
    || JSON.stringify(verified.config) !== JSON.stringify(data.config)
  ) {
    throw new Error("保存后回读配置不一致，请重试");
  }
  if (revision === submissionConfigRevision) {
    state.submissionConfig = verified.config;
  }
}

export async function loadStatus(): Promise<void> {
  const configRevision = submissionConfigRevision;
  state.loading = true;
  try {
    const [statusResult, configResult, submissionStatusResult] = await Promise.allSettled([
      adminApi.status(),
      submissionApi.getConfig(),
      submissionApi.status(),
    ]);
    if (statusResult.status === "fulfilled") {
      state.status = statusResult.value;
    }
    if (configResult.status === "fulfilled") {
      if (configRevision === submissionConfigRevision) {
        state.submissionConfig = configResult.value.config;
      }
      state.loaded = true;
    }
    if (submissionStatusResult.status === "fulfilled") {
      state.submissionStatus = submissionStatusResult.value;
    }
    const failures = [statusResult, configResult, submissionStatusResult]
      .filter((result) => result.status === "rejected");
    if (failures.length) {
      const configMessage = configResult.status === "rejected" ? "配置读取失败" : "部分状态读取失败";
      setToast(`${configMessage}，请点击刷新重试`, configResult.status === "rejected" ? "error" : "warn");
    } else {
      setToast("后端连接正常", "success");
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setToast(`读取状态失败：${message}`, "error");
  } finally {
    state.loading = false;
  }
}

export function useGlobalState() {
  return {
    state: readonly(state),
    confirmation: readonly(confirmation),
    loadStatus,
    setToast,
    notifySuccess,
    notifyError,
    confirm,
    resolveConfirmation,
    ensureSubmissionConfig,
    writeSubmissionConfig,
  };
}
