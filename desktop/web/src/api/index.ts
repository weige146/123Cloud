import { api, readJson } from "./client";
import type {
  AdminStatus,
  AccountCooldown,
  Pan115Device,
  Pan115DirectLinksStatus,
  Pan115HelperActionResult,
  Pan115HelperStatus,
  Pan123DirectLinksStatus,
  Pan123BrowseItem,
  Pan123BrowseResult,
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
  TelegramSessionStartResult,
  TelegramSessionVerifyResult,
} from "./types";

export interface MyChannelConfig {
  ownerUserId: number;
  channels: Channel[];
  routing: Routing;
  updatedAt?: string;
  createdAt?: string;
}

export interface MyChannelConfigUpdate {
  channels: Channel[];
  routing: Routing;
}

// ====== Admin / 123 网盘账号（OAuth 授权登录） ======
export const adminApi = {
  status: () => api.get<AdminStatus>("/api/admin/status"),
  oauthStart: () =>
    api.post<{ ok: boolean; authorizeUrl: string; redirectUri: string; nonce: string }>("/api/123/oauth/start", {}),
  oauthFinish: (nonce: string, callbackUrl: string) =>
    api.post<{ ok: boolean; authenticated: boolean; user: string; updatedAt: string; loginExpired?: boolean }>(
      "/api/123/oauth/finish",
      { nonce, callbackUrl }
    ),
  oauthImport: (refreshToken: string) =>
    api.post<{ ok: boolean; authenticated: boolean; user: string; updatedAt: string; loginExpired?: boolean }>(
      "/api/123/oauth/import",
      { refreshToken }
    ),
  logout: () => api.post<{ ok: boolean }>("/api/123/logout"),
};

// ====== 后端日志 ======
export const logsApi = {
  tail: (limit = 2000) => api.get<{ ok: boolean; logs: string[] }>(`/api/logs?limit=${limit}`),
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
  testTelegramApi: (payload: { apiId: string; apiHash: string; session: string }) =>
    api.post<{ ok: boolean; message: string }>("/api/submission/test/tg-api", payload),
  startTelegramSession: (payload: { apiId: string; apiHash: string; phone: string }) =>
    api.post<TelegramSessionStartResult>("/api/submission/telegram/session/start", payload),
  verifyTelegramSession: (payload: { loginId: string; code: string; password?: string }) =>
    api.post<TelegramSessionVerifyResult>("/api/submission/telegram/session/verify", payload),
  cancelTelegramSession: (loginId: string) =>
    api.post<{ ok: boolean }>("/api/submission/telegram/session/cancel", { loginId }),
  drafts: (limit = 100) => api.get<{ ok: boolean; drafts: SubmissionDraft[]; count: number }>(`/api/submission/drafts?limit=${limit}`),
  clearDrafts: () => api.delete<{ ok: boolean }>("/api/submission/drafts"),
  deleteDraft: (id: string) => api.delete<{ ok: boolean }>(`/api/submission/drafts/${encodeURIComponent(id)}`),
  submitDraft: (id: string) => api.post(`/api/submission/drafts/${encodeURIComponent(id)}/submit`, {}),
};

// 桌面端「投稿路由」管理接口：按频道主 UID 读写频道/路由配置。
export const channelOwnerApi = {
  owners: () => api.get<{ ok: boolean; owners: number[]; defaultOwnerUserId: number }>("/api/submission/channel-owners"),
  get: (userId: number) =>
    api.get<{ ok: boolean; config: MyChannelConfig }>(`/api/submission/channel-owners/${encodeURIComponent(String(userId))}`),
  put: (userId: number, config: MyChannelConfigUpdate) =>
    api.put<{ ok: boolean; config: MyChannelConfig }>(`/api/submission/channel-owners/${encodeURIComponent(String(userId))}`, config),
  delete: (userId: number) =>
    api.delete<{ ok: boolean; deleted: boolean }>(`/api/submission/channel-owners/${encodeURIComponent(String(userId))}`),
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
  dlinks: () => api.get<Pan115DirectLinksStatus>("/api/pan115-helper/dlinks"),
  dlinksOffline: (keys: string[]) => api.post<Pan115HelperActionResult>("/api/pan115-helper/dlinks/offline", { keys }),
  urlsOffline: (urls: string[]) => api.post<Pan115HelperActionResult>("/api/pan115-helper/urls/offline", { urls }),
  pan123Dlinks: () => api.get<Pan123DirectLinksStatus>("/api/pan115-helper/pan123/dlinks"),
  pan123DlinksOffline: (keys: string[]) => api.post<Pan115HelperActionResult>("/api/pan115-helper/pan123/dlinks/offline", { keys }),
  pan123Browse: (parentId = 0) => api.get<Pan123BrowseResult>(`/api/pan115-helper/pan123/browse?parent_id=${parentId || 0}`),
};

// ====== 115 搬运 ======
export const transferApi = {
  getConfig: () => api.get<TransferConfig>("/api/transfer/config"),
  putConfig: (config: TransferConfig) => api.put<{ ok: boolean; config: TransferConfig }>("/api/transfer/config", config),
  tasks: (limit = 100) => api.get<TransferTask[]>(`/api/transfer/tasks?limit=${limit}`),
  submit: (text: string) => api.post<{ ok: boolean; tasks: TransferTask[] }>("/api/transfer/tasks", { text }),
  submitLocal: (path115: string) => api.post<{ ok: boolean; task: TransferTask }>("/api/transfer/local-tasks", { path115 }),
  submit123to115: (sourceDirId: string) => api.post<{ ok: boolean; task: TransferTask }>("/api/transfer/123to115-tasks", { sourceDirId }),
  kick: () => api.post<{ ok: boolean }>("/api/transfer/kick", {}),
  requeue: (taskId: string) => api.post<{ ok: boolean; task: TransferTask }>(`/api/transfer/tasks/${encodeURIComponent(taskId)}/requeue`, {}),
  deleteTask: (taskId: string) => api.delete<{ ok: boolean }>(`/api/transfer/tasks/${encodeURIComponent(taskId)}`),
  offline: () => api.get<{ ok: boolean; tasks: TransferOfflineTask[]; canDelete: boolean }>("/api/transfer/offline"),
  deleteOffline: (taskId: number | string) => api.delete<{ ok: boolean }>(`/api/transfer/offline/${encodeURIComponent(String(taskId))}`),
  deleteCompletedOffline: () => api.delete<{ ok: boolean; deleted: number; message?: string }>("/api/transfer/offline/completed"),
  accountCooldowns: () => api.get<{ ok: boolean; accounts: AccountCooldown[]; cooldownMinutes: number }>("/api/transfer/account-cooldowns"),
  clearAccountCooldowns: () => api.delete<{ ok: boolean; cleared: number; accounts: string[] }>("/api/transfer/account-cooldowns"),
};

// ====== 秒传池（管理员自用：目录搜索 + SHA1 秒传） ======
export interface PoolSearchResult {
  sha1: string;
  size: number;
  name: string;
}

export interface PoolSearchResponse {
  available: boolean;
  results: PoolSearchResult[];
  cached: boolean;
  error?: string;
}

export interface PoolReuseItemResult {
  sha1: string;
  name: string;
  size: number;
  ok: boolean;
  fileId?: number;
  error?: string;
}

export interface PoolTokenStatus {
  override: boolean;
  overridePreview: string | null;
  defaultPreview: string | null;
}

export const poolApi = {
  search: (keyword: string, limit = 50) =>
    api.get<PoolSearchResponse>(`/api/pool/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),
  reuse: (dirId: string, items: PoolSearchResult[]) =>
    api.post<{ ok: boolean; results: PoolReuseItemResult[] }>("/api/pool/reuse", { dirId, items }),
  tokenStatus: () => api.get<PoolTokenStatus>("/api/pool/token"),
  setToken: (token: string) =>
    api.post<{ ok: boolean; override: boolean; overridePreview: string | null }>("/api/pool/token", { token }),
  resetToken: () => api.delete<{ ok: boolean; override: boolean }>("/api/pool/token"),
};

export interface PoolTokenStatus {
  override: boolean;
  overridePreview: string | null;
  defaultPreview: string | null;
}

// ====== 影库（影库文件解析入本地数据库，供管理页与油猴脚本搜索转存） ======
export interface LibraryWork {
  dir: string;
  title: string;
  year: number | null;
  tmdbId: number | null;
  count: number;
  videoCount: number;
  totalSize: number;
  cat: string;
  sub: string;
}

export interface LibraryCategory {
  name: string;
  count: number;
  size: number;
  subs: Array<{ name: string; count: number; size: number }>;
}

export interface LibraryFile {
  fileName: string;
  path: string;
  etag: string;
  size: number;
  isVideo: boolean;
}

export interface LibraryStatus {
  scanning?: boolean;
  lastScanAt?: number;
  lastScanError?: string;
  libCount: number;
  workCount: number;
  fileCount: number;
  videoCount: number;
  totalSize: number;
  totalSizeLabel: string;
}

export interface LibraryLibInfo {
  name: string;
  loadDate: string;
  fileCount: number;
  totalSize: number;
}

export interface LibraryImportResult {
  ok: boolean;
  name?: string;
  added: number;
  skipped: number;
  fileCount: number;
}

export interface LibraryConfig {
  transferIntervalMs: number;
  transferConcurrency: number;
  exportDir: string;
  tokenSet: boolean;
  token: string;
  tokenPreview: string | null;
}

export interface LibraryFastlinkJson {
  scriptVersion: string;
  exportVersion: string;
  usesBase62EtagsInExport: boolean;
  commonPath: string;
  totalFilesCount: number;
  totalSize: number;
  formattedTotalSize?: string;
  files: Array<{ path: string; fileName: string; etag: string; size: number; type: number; s3KeyFlag: string }>;
}

export interface LibraryShareItem {
  id: string;
  name: string;
  type: number;
  etag: string;
  size: number;
  s3KeyFlag: string;
}

export interface LibraryShareTask {
  taskId: string;
  status: string;
  createdAt?: number;
  progress: { step: string; files: number; dirs: number; skipped: number };
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface LibraryTransferTask {
  taskId: string;
  status: string;
  label: string;
  cancelRequested: boolean;
  progress: {
    step: string;
    done: number;
    total: number;
    success: number;
    missed: number;
    failed: number;
    workIndex: number;
    workCount: number;
    currentFile: string;
    targetPath: string;
    targetDirId: string;
    startedAt?: number;
    concurrency?: number;
    log: string[];
  };
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface TmdbDetail {
  title: string;
  year: number;
  overview: string;
  voteAverage: number;
  genres: string[];
  posterUrl: string;
}

export interface LibraryPickFolderResult {
  cancelled?: boolean;
  path?: string;
}

export const libraryApi = {
  getConfig: () => api.get<{ ok: boolean; config: LibraryConfig }>("/api/library/config"),
  putConfig: (config: Partial<LibraryConfig> & { clearToken?: boolean }) =>
    api.put<{ ok: boolean; config: LibraryConfig }>("/api/library/config", config),
  status: () => api.get<{ ok: boolean; status: LibraryStatus; libs: LibraryLibInfo[] }>("/api/library/status"),
  importPaths: (paths: string[], token: string) =>
    api.post<{ ok: boolean; results: Array<{ file: string; ok: boolean; added?: number; skipped?: number; error?: string }>; added: number; skipped: number; failed: number }>(
      "/api/library/import/paths",
      { paths, token },
    ),
  importDir: (path: string, token: string) =>
    api.post<{ ok: boolean; total: number; added: number; skipped: number; failed: number; results: Array<{ file: string; status: string; info: string }> }>(
      "/api/library/import/dir",
      { path, token },
    ),
  sources: () => api.get<{ ok: boolean; sources: LibraryLibInfo[] }>("/api/library/sources"),
  deleteSource: (name: string, token: string) =>
    api.post<{ ok: boolean }>("/api/library/sources/delete", { name, token }),
  categories: (token: string) =>
    api.get<{ ok: boolean; categories: LibraryCategory[] }>(`/api/library/categories${libraryTokenQuery(token)}`),
  search: (params: { q?: string; cat?: string; sub?: string; page?: number; size?: number; lib?: string; token?: string }) => {
    const query = new URLSearchParams();
    if (params.q) query.set("q", params.q);
    if (params.cat) query.set("cat", params.cat);
    if (params.sub) query.set("sub", params.sub);
    query.set("page", String(params.page ?? 1));
    query.set("size", String(params.size ?? 20));
    if (params.lib) query.set("lib", params.lib);
    if (params.token) query.set("token", params.token);
    return api.get<{ ok: boolean; total: number; page: number; size: number; dirs: LibraryWork[] }>(
      `/api/library/search?${query.toString()}`,
    );
  },
  files: (dir: string, token: string) =>
    api.get<{ ok: boolean; dir: string; title: string; year: number | null; tmdbId: number | null; files: LibraryFile[] }>(
      `/api/library/files?dir=${encodeURIComponent(dir)}${token ? `&token=${encodeURIComponent(token)}` : ""}`,
    ),
  exportJson: (params: { dir?: string; cat?: string; sub?: string; cats?: string; token?: string }) => {
    const query = new URLSearchParams();
    if (params.dir) query.set("dir", params.dir);
    if (params.cat) query.set("cat", params.cat);
    if (params.sub) query.set("sub", params.sub);
    if (params.cats) query.set("cats", params.cats);
    if (params.token) query.set("token", params.token);
    return api.get<{ ok: boolean; library: LibraryFastlinkJson }>(`/api/library/export?${query.toString()}`);
  },
  exportSave: (payload: { dir?: string; cat?: string; sub?: string; cats?: string; includeFiles?: string[]; label?: string; token?: string }) =>
    api.post<{ ok: boolean; file: string; path: string; totalFilesCount: number; formattedTotalSize: string }>(
      "/api/library/export/save",
      payload,
    ),
  openExportDir: (path: string, token: string) =>
    api.post<{ ok: boolean; path: string }>("/api/library/export/open", { path, token }),
  poster: (tmdbId: number, title: string, year: number, token: string) =>
    api.get<{ ok: boolean; url: string }>(
      `/api/library/poster?tmdbId=${tmdbId}&title=${encodeURIComponent(title)}&year=${year || 0}${token ? `&token=${encodeURIComponent(token)}` : ""}`,
    ),
  tmdbDetail: (tmdbId: number, title: string, year: number, token: string) =>
    api.get<{ ok: boolean; detail: TmdbDetail | null }>(
      `/api/library/tmdb/${tmdbId}?title=${encodeURIComponent(title)}&year=${year || 0}${token ? `&token=${encodeURIComponent(token)}` : ""}`,
    ),
  shareHistory: (token: string) =>
    api.get<{ ok: boolean; tasks: LibraryShareTask[] }>(
      `/api/library/share/history${token ? `?token=${encodeURIComponent(token)}` : ""}`,
    ),
  shareBrowse: (url: string, parentId: string, token: string, page = 1) =>
    api.post<{ ok: boolean; shareKey: string; parentId: string; items: LibraryShareItem[]; hasMore: boolean }>(
      "/api/library/share/browse",
      { url, parentId, page, token },
    ),
  shareExtract: (payload: {
    url: string;
    cat?: string;
    sub?: string;
    title?: string;
    selectedItems?: LibraryShareItem[];
    fileFilters?: string[];
    resume?: boolean;
    token?: string;
  }) => api.post<{ ok: boolean; taskId: string }>("/api/library/share/extract", payload),
  shareTask: (taskId: string, token: string) =>
    api.get<{ ok: boolean; task: LibraryShareTask }>(
      `/api/library/share/task?taskId=${encodeURIComponent(taskId)}${token ? `&token=${encodeURIComponent(token)}` : ""}`,
    ),
  shareCheckpoint: (url: string, selectedItems: LibraryShareItem[], token: string) =>
    api.post<{ ok: boolean; checkpoint: { hasCheckpoint: boolean; totalFiles?: number; updatedAt?: string } }>(
      "/api/library/share/checkpoint",
      { url, selectedItems, token },
    ),
  deleteShareCheckpoint: (url: string, selectedItems: LibraryShareItem[], token: string) =>
    api.post<{ ok: boolean; deleted: boolean }>("/api/library/share/checkpoint/delete", {
      url,
      selectedItems,
      token,
    }),
  transfer: (dirs: string[], targetPath: string, targetDirId: string, token: string, includeFiles?: string[]) =>
    api.post<{ ok: boolean; taskId: string; workCount: number; fileCount: number }>("/api/library/transfer", {
      dirs,
      includeFiles: includeFiles || [],
      targetPath,
      targetDirId,
      token,
    }),
  transferTask: (taskId: string, token: string) =>
    api.get<{ ok: boolean; task: LibraryTransferTask }>(
      `/api/library/transfer/task?taskId=${encodeURIComponent(taskId)}${token ? `&token=${encodeURIComponent(token)}` : ""}`,
    ),
  transferCancel: (taskId: string, token: string) =>
    api.post<{ ok: boolean }>("/api/library/transfer/cancel", { taskId, token }),
  importFile: async (name: string, content: string, token: string) => {
    const query = new URLSearchParams({ name });
    if (token) query.set("token", token);
    const response = await fetch(`/api/library/import?${query.toString()}`, {
      method: "POST",
      headers: { "content-type": "text/plain; charset=utf-8" },
      body: content,
    });
    return readJson<{ ok: boolean; name: string; added: number; skipped: number; fileCount: number }>(response);
  },
};

function libraryTokenQuery(token: string): string {
  return token ? `?token=${encodeURIComponent(token)}` : "";
}
