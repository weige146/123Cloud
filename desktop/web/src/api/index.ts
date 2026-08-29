import { api, apiTelegramGet, apiTelegramJson } from "./client";
import type {
  AdminConfig,
  AdminStatus,
  Pan115Device,
  Pan115HelperStatus,
  SubmissionConfig,
  SubmissionDraft,
  SubmissionStatus,
  SubmissionDisplayPreview,
  SubmissionDisplayPreviewSample,
  TransferConfig,
  TransferOfflineTask,
  TransferTask,
  Channel,
  Routing,
  WallpaperResponse,
} from "./types";

export interface MyChannelConfig {
  ownerUserId: number;
  channels: Channel[];
  routing: Routing;
  updatedAt?: string;
  createdAt?: string;
  canManageChannelOwners?: boolean;
  channelOwnerUserIds?: number[];
}

export interface MyChannelConfigUpdate {
  channels: Channel[];
  routing: Routing;
  channelOwnerUserIds?: number[];
}

// ====== Admin / 网关 ======
export const adminApi = {
  wallpapers: () => api.get<WallpaperResponse>("/api/admin/wallpapers"),
  status: () => api.get<AdminStatus>("/api/admin/status"),
  getConfig: () => api.get<{ ok: boolean; config: AdminConfig }>("/api/admin/config"),
  putConfig: (config: AdminConfig) => api.put<{ ok: boolean; config: AdminConfig }>("/api/admin/config", config),
  login: (user: string, password: string, remember: boolean) =>
    api.post<{ ok: boolean; user: string; loginUuid: string; reused: boolean; updatedAt: string }>(
      "/api/123/login",
      { user, password, remember }
    ),
  logout: () => api.post<{ ok: boolean }>("/api/123/logout"),
};

// ====== 投稿 ======
export const submissionApi = {
  getConfig: (fresh = false) => api.get<{ ok: boolean; config: SubmissionConfig }>(
    `/api/submission/config${fresh ? `?verify=${Date.now()}` : ""}`
  ),
  putConfig: (config: SubmissionConfig) => api.put<{ ok: boolean; config: SubmissionConfig }>("/api/submission/config", config),
  previewDisplay: (config: SubmissionConfig, sample?: SubmissionDisplayPreviewSample) =>
    api.post<{ ok: boolean; preview: SubmissionDisplayPreview }>("/api/submission/display/preview", { config, sample }),
  status: () => api.get<SubmissionStatus>("/api/submission/status"),
  testBot: (token: string) => api.post<{ ok: boolean; message: string }>("/api/submission/test/bot", { token }),
  drafts: (limit = 100) => api.get<{ ok: boolean; drafts: SubmissionDraft[]; count: number }>(`/api/submission/drafts?limit=${limit}`),
  clearDrafts: () => api.delete<{ ok: boolean }>("/api/submission/drafts"),
  deleteDraft: (id: string) => api.delete<{ ok: boolean }>(`/api/submission/drafts/${encodeURIComponent(id)}`),
  submitDraft: (id: string) => api.post(`/api/submission/drafts/${encodeURIComponent(id)}/submit`, {}),
};

// This API intentionally has no UID parameter.  Telegram Web App initData is
// verified by the server and decides which owner's configuration is visible.
export const telegramChannelApi = {
  get: () => apiTelegramGet<{ ok: boolean; config: MyChannelConfig }>("/api/submission/my-channel-config"),
  put: (config: MyChannelConfigUpdate) =>
    apiTelegramJson<{ ok: boolean; config: MyChannelConfig }>("/api/submission/my-channel-config", "PUT", config),
  delete: () => apiTelegramJson<{ ok: boolean; deleted: boolean }>("/api/submission/my-channel-config", "DELETE"),
};

// ====== 115 Cookie 扫码 ======
export const pan115CookieApi = {
  devices: () => api.get<{ ok: boolean; devices: Pan115Device[] }>("/api/pan115-cookie/devices"),
  createSession: (device: string) => api.post<{ ok: boolean; sessionId: string; qrcodeDataUrl: string; scanUrl?: string; expiresAt: string; deviceLabel: string }>(
    "/api/pan115-cookie/sessions",
    { device }
  ),
  status: (sessionId: string) =>
    api.get<{ ok: boolean; status: number; statusText: string; expiresAt: string; expiresInMs: number }>(
      `/api/pan115-cookie/sessions/${encodeURIComponent(sessionId)}/status`
    ),
  confirm: (sessionId: string) =>
    api.post<{ ok: boolean; cookieText: string; cookieJson?: unknown[] }>(`/api/pan115-cookie/sessions/${encodeURIComponent(sessionId)}/confirm`, {}),
};

// ====== 115 助手 ======
export const pan115HelperApi = {
  status: () => api.get<Pan115HelperStatus>("/api/pan115-helper/status"),
  offline: (text: string) => api.post("/api/pan115-helper/offline", { text }),
  emptyRecycle: () => api.post("/api/pan115-helper/recycle/empty", {}),
};

// ====== 115 搬运 ======
export const transferApi = {
  getConfig: () => api.get<TransferConfig>("/api/transfer/config"),
  putConfig: (config: TransferConfig) => api.put<{ ok: boolean; config: TransferConfig }>("/api/transfer/config", config),
  tasks: (limit = 100) => api.get<TransferTask[]>(`/api/transfer/tasks?limit=${limit}`),
  submit: (text: string) => api.post<{ ok: boolean; tasks: TransferTask[] }>("/api/transfer/tasks", { text }),
  submitLocal: (path115: string) => api.post<{ ok: boolean; task: TransferTask }>("/api/transfer/local-tasks", { path115 }),
  kick: () => api.post<{ ok: boolean }>("/api/transfer/kick", {}),
  requeue: (taskId: string) => api.post<{ ok: boolean; task: TransferTask }>(`/api/transfer/tasks/${encodeURIComponent(taskId)}/requeue`, {}),
  deleteTask: (taskId: string) => api.delete<{ ok: boolean }>(`/api/transfer/tasks/${encodeURIComponent(taskId)}`),
  offline: () => api.get<{ ok: boolean; tasks: TransferOfflineTask[]; canDelete: boolean }>("/api/transfer/offline"),
  deleteOffline: (taskId: number | string) => api.delete<{ ok: boolean }>(`/api/transfer/offline/${encodeURIComponent(String(taskId))}`),
  deleteCompletedOffline: () => api.delete<{ ok: boolean; deleted: number; message?: string }>("/api/transfer/offline/completed"),
};
