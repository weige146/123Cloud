// 后端 API 类型定义
// 与 backend/app/main.py 中的 Pydantic 模型对齐

export interface Pan123Profile {
  uid?: number | null;
  nickname?: string;
  headImage?: string;
  passport?: string;
  mail?: string;
  spaceUsed?: number | null;
  spacePermanent?: number | null;
  spaceTemp?: number | null;
  spaceTempExpr?: string;
  vip?: number | null;
  directTraffic?: number | null;
  isHideUID?: boolean | null;
  httpsCount?: number | null;
}

export interface Pan123Session {
  backend: boolean;
  authenticated: boolean;
  user: string;
  loginUuid: string;
  updatedAt: string;
  profile: Pan123Profile | null;
  /** 登录 token 已过期：profile 无法刷新，需重新登录（头像等缓存链接会失效） */
  loginExpired?: boolean;
}

export interface Capabilities {
  submissionConfigured: boolean;
  pan115HelperConfigured?: boolean;
  transferConfigured?: boolean;
}

export interface AdminStatus {
  ok: boolean;
  capabilities: Capabilities;
  pan123: Pan123Session;
}

export interface Channel {
  id: string;
  title: string;
  chatId: string;
  role: "private" | "public_completed" | "public_updating" | string;
  enabled: boolean;
  isDefault?: boolean;
  allowedUserIds?: number[];
  [key: string]: unknown;
}

export interface Routing {
  releaseGroupChannelId?: string;
  noReleaseGroupCompletedChannelId?: string;
  noReleaseGroupUpdatingChannelId?: string;
  fallbackChannelId?: string;
  publicReleaseGroups?: string[];
}

export interface AliasRule {
  id?: string;
  enabled: boolean;
  value: string;
  aliases: string[];
  order?: number;
  rank?: string;
  priority?: number;
}

export interface RecognitionRule {
  id: string;
  name: string;
  enabled: boolean;
  pattern: string;
  flags: string;
}

export interface SourceLabel {
  id?: string;
  name?: string;
  enabled: boolean;
  source: string;
  template: string;
  order?: number;
}

export interface RecognitionConfig {
  movieKeywords?: string[];
  tvKeywords?: string[];
  releaseGroups?: string[];
  excludeWords?: string[];
}

export interface DisplayConfig {
  sourceLabels?: SourceLabel[];
}

export interface RuleConfig {
  recognition?: RecognitionConfig;
  display?: DisplayConfig;
  quality?: AliasRule[];
  source?: AliasRule[];
  effect?: AliasRule[];
  webSource?: AliasRule[];
  videoCodec?: AliasRule[];
  audioCodec?: AliasRule[];
  edition?: AliasRule[];
}

export interface TelegramApi {
  apiId?: string;
  apiHash?: string;
  session?: string;
}

export interface Templates {
  shareName?: string;
  shareUrl?: string;
  caption?: string;
}

export interface Pan115HelperConfig {
  enabled?: boolean;
  pan115Cookie?: string;
  pan115Cookies?: string[];
  offlineTargetDirId?: string;
  trashPassword?: string;
  dailyRecycleCleanupEnabled?: boolean;
  dailyRecycleCleanupTime?: string;
  dailyRecycleCleanupTimeZone?: string;
  requestIntervalMs?: number;
}

export interface SubmissionConfig {
  botToken?: string;
  tmdbToken?: string;
  tmdbLanguage?: string;
  telegramApi?: TelegramApi;
  allowedUserIds?: number[];
  telegramAdminUserIds?: number[];
  channelOwnerUserIds?: number[];
  channels?: Channel[];
  routing?: Routing;
  ruleConfig?: RuleConfig;
  recognitionRules?: RecognitionRule[];
  templates?: Templates;
  pan115Helper?: Pan115HelperConfig;
  system?: Record<string, unknown>;
  updatedAt?: string;
}

export interface SubmissionStatus {
  ok: boolean;
  botConfigured: boolean;
  telegramApiConfigured: boolean;
  tmdbConfigured: boolean;
  allowedUserCount: number;
  channelCount: number;
  userChannelCount: number;
  draftCount: number;
  shareName: string;
  updatedAt: string;
}

export interface SubmissionDisplayPreviewSample {
  title?: string;
  year?: string;
  mediaType?: "movie" | "tv" | string;
  quality?: string;
  source?: string;
  resourceType?: string;
  webSource?: string;
  effect?: string;
  fps?: string;
  videoCodec?: string;
  audioCodec?: string;
  size?: string;
  releaseGroup?: string;
  seasonEpisode?: string;
  overview?: string;
  fileNames?: string[];
  shareUrl?: string;
  [key: string]: unknown;
}

export interface SubmissionDisplayPreview {
  caption: string;
  text?: string;
  resourceName: string;
  sourceLabel?: string;
  shareLink?: string;
  routeChannel?: string;
  resourceBlock?: string;
  overviewBlock?: string;
  [key: string]: unknown;
}

export interface SubmissionDraft {
  id: string;
  sourceLabel?: string;
  caption?: string;
  text?: string;
  sent?: boolean;
  linkCount?: number;
  channelTitle?: string;
  createdAt?: string;
}

export interface Pan115Device {
  id: string;
  label: string;
}

export interface Pan115HelperStatus {
  ok: boolean;
  enabled: boolean;
  message?: string;
  accountName?: string;
  userId?: string;
}

export interface TransferConfig {
  enabled: boolean;
  pan123ClientId: string;
  pan123ClientSecret: string;
  pan115Cookie: string;
  pan115Cookies: string[];
  targetDirId: string;
  localPath115: string;
  excludeSuffix: string;
  excludeCid: string;
  delete115AfterSuccess: boolean;
  concurrency: number;
  pauseEnabled: boolean;
  pauseTimeZone: string;
  pauseStartHour: number;
  pauseEndHour: number;
  downloadMinIntervalMs: number;
  downloadMaxAttempts: number;
  downloadRetryBaseMs: number;
  offlinePollMs: number;
  offlineMaxPolls: number;
  progressNotifyIntervalMs: number;
}

export interface TransferTaskFile {
  id: string;
  name: string;
  size?: number;
  sha1?: string | null;
  path?: string[];
  sourceType?: string;
  pickCode?: string;
  localFileId?: string;
  pan115Deleted?: boolean;
  pan115DeleteError?: string | null;
  status?: string;
  method?: string | null;
  pan123FileId?: string | number | null;
  offlineTaskId?: string | number | null;
  offlineStatus?: string | number | null;
  offlineStatusText?: string;
  offlineProgress?: number | null;
  error?: string | null;
  startedAt?: string;
  finishedAt?: string;
}

export interface TransferTaskLog {
  time?: string;
  level?: string;
  message?: string;
}

export interface TransferTask {
  id: string;
  kind?: string;
  source?: string;
  sourceText?: string;
  shareUrl?: string;
  shareCode?: string;
  receiveCode?: string | null;
  title?: string;
  remoteTaskId?: string | number | null;
  targetDirId?: string;
  shareOwnerUserId?: string | number | null;
  status: "queued" | "running" | "success" | "partial" | "failed" | string;
  totalFiles?: number;
  doneFiles?: number;
  files?: TransferTaskFile[];
  logs?: TransferTaskLog[];
  error?: string | null;
  createdAt?: string;
  updatedAt?: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface TransferOfflineTask {
  id?: number | string;
  name?: string;
  status?: string | number;
  statusText?: string;
  progress?: number;
  size?: number;
  message?: string;
}

export interface AccountCooldown {
  name: string;
  remainingMinutes: number;
}
