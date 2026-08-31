// 桌面端自动更新状态：主进程 electron-updater 事件的渲染层封装。
// 模块级单例：App.vue 与 SettingsView 共用同一份状态与监听器。
import { reactive } from "vue";

export type UpdateStatus =
  | "idle"
  | "checking"
  | "downloading"
  | "downloaded"
  | "installing"
  | "none"
  | "update-manual"
  | "error";

export interface UpdateState {
  status: UpdateStatus;
  info: { version?: string } | null;
  error: string;
  percent?: number;
}

interface UpdaterBridge {
  getUpdateState?: () => Promise<UpdateState>;
  checkForUpdates?: () => Promise<UpdateState>;
  installUpdate?: () => Promise<boolean>;
  onUpdateStatus?: (callback: (payload: UpdateState) => void) => void;
}

function updaterBridge(): UpdaterBridge | undefined {
  return (window as unknown as { cloud123?: UpdaterBridge }).cloud123;
}

const state = reactive<UpdateState>({ status: "idle", info: null, error: "" });
let initialized = false;

function applyUpdate(payload: UpdateState) {
  state.status = payload.status ?? state.status;
  state.info = payload.info ?? state.info;
  state.error = payload.error || "";
  state.percent = typeof payload.percent === "number" ? payload.percent : undefined;
}

export function useUpdater() {
  const api = updaterBridge();
  if (api && !initialized) {
    initialized = true;
    api.getUpdateState?.().then(applyUpdate);
    api.onUpdateStatus?.(applyUpdate);
  }
  return {
    state,
    // 浏览器模式没有桌面桥，整个更新 UI 隐藏
    available: Boolean(api?.checkForUpdates),
    check: () => api?.checkForUpdates?.() ?? Promise.resolve(),
    install: () => api?.installUpdate?.() ?? Promise.resolve(false),
  };
}
