<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { telegramChannelApi, type MyChannelConfig, type MyChannelConfigUpdate } from "@/api";
import type { Channel, Routing } from "@/api/types";
import { useGlobalState } from "@/composables/useGlobalState";

type TelegramWebApp = {
  initData?: string;
  initDataUnsafe?: { user?: { first_name?: string; username?: string } };
  ready?: () => void;
  expand?: () => void;
  close?: () => void;
};

type EditableChannel = Channel & { collaboratorText: string };

const telegram = ref<TelegramWebApp>();
const loading = ref(true);
const saving = ref(false);
const removingAll = ref(false);
const error = ref("");
const notice = ref("");
const updatedAt = ref("");
const canManageChannelOwners = ref(false);
const channelOwnerIdsText = ref("");
const channels = ref<EditableChannel[]>([]);
const { confirm } = useGlobalState();
const releaseGroupsText = ref("");
const routing = reactive<Routing>({
  releaseGroupChannelId: "",
  noReleaseGroupCompletedChannelId: "",
  noReleaseGroupUpdatingChannelId: "",
  fallbackChannelId: "",
});

const hasTelegramIdentity = computed(() => Boolean(telegram.value?.initData));
const channelOptions = computed(() => [
  { title: "不设置", value: "" },
  ...channels.value.map((channel) => ({ title: String(channel.title ?? "").trim() || "未命名频道", value: channel.id })),
]);

const roleItems = [
  { title: "私有频道", value: "private" },
  { title: "公开频道（完结内容）", value: "public_completed" },
  { title: "公开频道（连载内容）", value: "public_updating" },
];

function loadTelegramBridge(): Promise<void> {
  if ((window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp) return Promise.resolve();
  const existing = document.querySelector<HTMLScriptElement>('script[data-telegram-web-app]');
  if (existing) {
    return new Promise((resolve) => {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => resolve(), { once: true });
    });
  }
  return new Promise((resolve) => {
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-web-app.js?56";
    script.async = true;
    script.dataset.telegramWebApp = "true";
    script.addEventListener("load", () => resolve(), { once: true });
    script.addEventListener("error", () => resolve(), { once: true });
    document.head.appendChild(script);
  });
}

const routeKeys = [
  "releaseGroupChannelId",
  "noReleaseGroupCompletedChannelId",
  "noReleaseGroupUpdatingChannelId",
  "fallbackChannelId",
] as const;

const enabledChannelCount = computed(() => channels.value.filter((c) => c.enabled !== false).length);

function parseIds(text: string): number[] {
  const ids: number[] = [];
  for (const value of text.split(/[\n,，\s]+/)) {
    const id = Number(value.trim());
    if (Number.isSafeInteger(id) && id > 0 && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

function parseLines(text: string): string[] {
  return Array.from(new Set(text.split(/\n+/).map((value) => value.trim()).filter(Boolean)));
}

function makeChannelId(): string {
  return `channel_${Date.now()}_${channels.value.length + 1}`;
}

function editableChannel(value: Channel): EditableChannel {
  return {
    id: String(value.id || makeChannelId()),
    title: String(value.title || ""),
    chatId: String(value.chatId || ""),
    role: value.role || "private",
    enabled: value.enabled !== false,
    isDefault: Boolean(value.isDefault),
    collaboratorText: (value.allowedUserIds || []).join("\n"),
  };
}

function applyConfig(config: MyChannelConfig) {
  channels.value = (config.channels || []).map(editableChannel);
  if (!channels.value.length) addChannel();
  const source = config.routing || {};
  routing.releaseGroupChannelId = String(source.releaseGroupChannelId || "");
  routing.noReleaseGroupCompletedChannelId = String(source.noReleaseGroupCompletedChannelId || "");
  routing.noReleaseGroupUpdatingChannelId = String(source.noReleaseGroupUpdatingChannelId || "");
  routing.fallbackChannelId = String(source.fallbackChannelId || "");
  releaseGroupsText.value = (source.publicReleaseGroups || []).join("\n");
  updatedAt.value = config.updatedAt || "";
  canManageChannelOwners.value = config.canManageChannelOwners === true;
  channelOwnerIdsText.value = (config.channelOwnerUserIds || []).join("\n");
}

function addChannel() {
  channels.value.push({
    id: makeChannelId(),
    title: "",
    chatId: "",
    role: "private",
    enabled: true,
    isDefault: channels.value.length === 0,
    collaboratorText: "",
  });
}

function setDefault(target: EditableChannel, enabled: boolean) {
  if (!enabled) {
    target.isDefault = false;
    return;
  }
  channels.value.forEach((channel) => { channel.isDefault = channel === target; });
}

function removeChannel(target: EditableChannel) {
  if (channels.value.length === 1) {
    error.value = "请至少保留一个频道卡片；不需要时可使用底部的“清空我的配置”。";
    return;
  }
  const index = channels.value.indexOf(target);
  if (index < 0) return;
  const deletedId = target.id;
  const wasDefault = Boolean(target.isDefault);
  channels.value.splice(index, 1);
  routeKeys.forEach((key) => {
    if (routing[key] === deletedId) routing[key] = "";
  });
  if (wasDefault) channels.value[0].isDefault = true;
}

function buildPayload(): MyChannelConfigUpdate | null {
  const ids = new Set<string>();
  const result: Channel[] = [];
  for (const channel of channels.value) {
    const title = String(channel.title ?? "").trim();
    const chatId = String(channel.chatId ?? "").trim();
    if (!title || !chatId) {
      error.value = "每个频道都需要填写“显示名称”和“频道 Chat ID”。";
      return null;
    }
    if (ids.has(channel.id)) {
      error.value = "频道保存失败：检测到重复频道。";
      return null;
    }
    ids.add(channel.id);
    result.push({
      id: channel.id,
      title,
      chatId,
      role: channel.role || "private",
      enabled: channel.enabled !== false,
      isDefault: Boolean(channel.isDefault),
      allowedUserIds: parseIds(channel.collaboratorText),
    });
  }
  const knownIds = new Set(result.map((channel) => channel.id));
  const selectedRoute = (value: unknown) => {
    const id = String(value || "");
    return knownIds.has(id) ? id : "";
  };
  const payload: MyChannelConfigUpdate = {
    channels: result,
    routing: {
      releaseGroupChannelId: selectedRoute(routing.releaseGroupChannelId),
      noReleaseGroupCompletedChannelId: selectedRoute(routing.noReleaseGroupCompletedChannelId),
      noReleaseGroupUpdatingChannelId: selectedRoute(routing.noReleaseGroupUpdatingChannelId),
      fallbackChannelId: selectedRoute(routing.fallbackChannelId),
      publicReleaseGroups: parseLines(releaseGroupsText.value),
    },
  };
  if (canManageChannelOwners.value) payload.channelOwnerUserIds = parseIds(channelOwnerIdsText.value);
  return payload;
}

async function load() {
  if (!hasTelegramIdentity.value) {
    error.value = "请回到 Telegram Bot，发送 /channels 后点击“打开我的频道配置”。";
    loading.value = false;
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    const data = await telegramChannelApi.get();
    applyConfig(data.config);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function save() {
  const payload = buildPayload();
  if (!payload) return;
  saving.value = true;
  error.value = "";
  notice.value = "";
  try {
    const data = await telegramChannelApi.put(payload);
    applyConfig(data.config);
    notice.value = "已保存。新的投稿和自动路由会立刻按本页设置生效。";
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    saving.value = false;
  }
}

async function clearAll() {
  if (!(await confirm("确定清空自己的频道与路由配置吗？不会删除 Telegram 历史消息或历史发布记录。", "清空频道配置"))) return;
  removingAll.value = true;
  error.value = "";
  notice.value = "";
  try {
    await telegramChannelApi.delete();
    channels.value = [];
    routing.releaseGroupChannelId = "";
    routing.noReleaseGroupCompletedChannelId = "";
    routing.noReleaseGroupUpdatingChannelId = "";
    routing.fallbackChannelId = "";
    releaseGroupsText.value = "";
    addChannel();
    updatedAt.value = "";
    notice.value = "已清空配置；Telegram 中已有的消息和发布记录没有被删除。";
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    removingAll.value = false;
  }
}

onMounted(async () => {
  await loadTelegramBridge();
  telegram.value = (window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp;
  telegram.value?.ready?.();
  telegram.value?.expand?.();
  load();
});
</script>

<template>
  <main class="channel-settings">
      <!-- 装饰背景 -->
      <div class="bg-mesh bg-mesh-1" aria-hidden="true" />
      <div class="bg-mesh bg-mesh-2" aria-hidden="true" />
      <div class="bg-grid" aria-hidden="true" />

      <div class="settings-shell">
        <!-- 顶部英雄区 -->
        <header class="settings-hero">
          <div class="hero-left">
            <div class="hero-eyebrow">
              <v-icon size="14">mdi-account-circle-outline</v-icon>
              <span>我的投稿空间</span>
            </div>
            <h1 class="hero-title">我的频道配置</h1>
            <p class="hero-desc">在这里一次设置频道、自动路由、发布组白名单和可投稿的协作者。</p>
          </div>
          <v-btn icon="mdi-close" variant="text" aria-label="关闭" class="hero-close" @click="telegram?.close?.()" />
        </header>

        <!-- 顶部统计条 -->
        <div class="stat-row">
          <div class="stat-pill">
            <div class="stat-pill-icon"><v-icon size="16">mdi-broadcast</v-icon></div>
            <div class="stat-pill-body">
              <span class="stat-pill-value">{{ channels.length }}</span>
              <span class="stat-pill-label">频道</span>
            </div>
          </div>
          <div class="stat-pill">
            <div class="stat-pill-icon stat-pill-icon--success"><v-icon size="16">mdi-check-circle-outline</v-icon></div>
            <div class="stat-pill-body">
              <span class="stat-pill-value">{{ enabledChannelCount }}</span>
              <span class="stat-pill-label">启用中</span>
            </div>
          </div>
          <div class="stat-pill">
            <div class="stat-pill-icon stat-pill-icon--info"><v-icon size="16">mdi-routes-outline</v-icon></div>
            <div class="stat-pill-body">
              <span class="stat-pill-value">4</span>
              <span class="stat-pill-label">路由规则</span>
            </div>
          </div>
          <div v-if="updatedAt" class="stat-pill">
            <div class="stat-pill-icon"><v-icon size="16">mdi-clock-outline</v-icon></div>
            <div class="stat-pill-body">
              <span class="stat-pill-value stat-pill-value--time">{{ updatedAt }}</span>
              <span class="stat-pill-label">上次保存</span>
            </div>
          </div>
        </div>

        <!-- 消息条 -->
        <v-progress-linear v-if="loading" indeterminate color="primary" class="settings-progress" />
        <v-alert v-if="error" type="error" variant="tonal" class="message-alert" closable @click:close="error = ''">{{ error }}</v-alert>
        <v-alert v-if="notice" type="success" variant="tonal" class="message-alert" closable @click:close="notice = ''">{{ notice }}</v-alert>

        <template v-if="!loading && hasTelegramIdentity">
          <v-alert type="info" variant="tonal" class="message-alert">
            这里只有你的配置。填入“允许投稿的 UID”的人只能投稿到对应频道，不能查看或修改你的频道、路由与发布组名单。
          </v-alert>

          <!-- 频道所有者 -->
          <section v-if="canManageChannelOwners" class="glass-section">
            <header class="section-head">
              <div class="section-head-left">
                <div class="section-icon section-icon--group"><v-icon size="18">mdi-account-supervisor-outline</v-icon></div>
                <div>
                  <h2>频道所有者</h2>
                  <p>这里添加的人可管理自己的频道和路由；他们不会自动获得搬运、115 或你的频道权限。</p>
                </div>
              </div>
            </header>
            <v-textarea
              v-model="channelOwnerIdsText"
              label="允许管理自己频道的 Telegram UID"
              placeholder="每行一个 UID；朋友可先向 Bot 发送 /myid 获取"
              :rows="3"
              variant="outlined"
              density="comfortable"
              hide-details
            />
          </section>

          <!-- 频道列表 -->
          <section class="glass-section">
            <header class="section-head">
              <div class="section-head-left">
                <div class="section-icon section-icon--group"><v-icon size="18">mdi-broadcast</v-icon></div>
                <div>
                  <h2>频道</h2>
                  <p>名称只供你识别；Chat ID 是 Telegram 频道的数字 ID，例如 <code>-100xxxxxxxxxx</code>。</p>
                </div>
              </div>
              <v-btn color="primary" prepend-icon="mdi-plus" @click="addChannel">添加频道</v-btn>
            </header>

            <div class="channel-grid">
              <article v-for="(channel, index) in channels" :key="channel.id" class="channel-card" :class="{ 'channel-card--default': channel.isDefault }">
                <div class="channel-card-head">
                  <div class="channel-card-title">
                    <span class="channel-card-index">#{{ index + 1 }}</span>
                    <strong v-if="channel.title">{{ channel.title }}</strong>
                    <strong v-else class="muted">未命名频道</strong>
                    <span v-if="channel.isDefault" class="default-badge">
                      <v-icon size="11">mdi-star</v-icon>
                      默认
                    </span>
                  </div>
                  <div class="channel-actions">
                    <v-switch
                      :model-value="channel.isDefault"
                      label="默认投稿"
                      color="primary"
                      density="compact"
                      hide-details
                      @update:model-value="setDefault(channel, Boolean($event))"
                    />
                    <v-btn size="small" color="error" variant="text" icon="mdi-delete-outline" aria-label="删除频道" @click="removeChannel(channel)" />
                  </div>
                </div>
                <div class="field-grid">
                  <v-text-field v-model="channel.title" label="显示名称" placeholder="例如：我的私有频道" variant="outlined" density="comfortable" hide-details />
                  <v-text-field v-model="channel.chatId" label="频道 Chat ID" placeholder="-100xxxxxxxxxx" variant="outlined" density="comfortable" hide-details />
                  <v-select v-model="channel.role" label="频道类型" :items="roleItems" variant="outlined" density="comfortable" hide-details />
                  <v-switch v-model="channel.enabled" label="启用这个频道" color="primary" density="comfortable" hide-details />
                </div>
                <v-textarea
                  v-model="channel.collaboratorText"
                  label="允许投稿的 Telegram UID（可留空）"
                  placeholder="每行一个 UID；这些人只能投稿到这个频道"
                  :rows="2"
                  variant="outlined"
                  density="comfortable"
                  hide-details
                  class="collaborator-field"
                />
              </article>
            </div>
          </section>

          <!-- 自动路由 -->
          <section class="glass-section">
            <header class="section-head">
              <div class="section-head-left">
                <div class="section-icon section-icon--info"><v-icon size="18">mdi-routes-outline</v-icon></div>
                <div>
                  <h2>自动路由</h2>
                  <p>系统会按内容特征自动选择目标频道；不需要的规则可以保留为“不设置”。</p>
                </div>
              </div>
            </header>
            <div class="route-grid">
              <div class="route-item">
                <div class="route-icon route-icon--group"><v-icon size="16">mdi-check-decagram</v-icon></div>
                <div class="route-body">
                  <label>命中发布组白名单时投递到</label>
                  <v-select v-model="routing.releaseGroupChannelId" :items="channelOptions" variant="outlined" density="comfortable" hide-details />
                </div>
              </div>
              <div class="route-item">
                <div class="route-icon route-icon--success"><v-icon size="16">mdi-check-circle</v-icon></div>
                <div class="route-body">
                  <label>无发布组的完结内容投递到</label>
                  <v-select v-model="routing.noReleaseGroupCompletedChannelId" :items="channelOptions" variant="outlined" density="comfortable" hide-details />
                </div>
              </div>
              <div class="route-item">
                <div class="route-icon route-icon--info"><v-icon size="16">mdi-progress-clock</v-icon></div>
                <div class="route-body">
                  <label>无发布组的连载内容投递到</label>
                  <v-select v-model="routing.noReleaseGroupUpdatingChannelId" :items="channelOptions" variant="outlined" density="comfortable" hide-details />
                </div>
              </div>
              <div class="route-item">
                <div class="route-icon route-icon--warning"><v-icon size="16">mdi-shield-outline</v-icon></div>
                <div class="route-body">
                  <label>其余内容（兜底）投递到</label>
                  <v-select v-model="routing.fallbackChannelId" :items="channelOptions" variant="outlined" density="comfortable" hide-details />
                </div>
              </div>
            </div>
            <v-textarea
              v-model="releaseGroupsText"
              label="发布组白名单"
              placeholder="每行一个发布组名称；命中后使用上面的“命中发布组白名单时”规则"
              :rows="3"
              variant="outlined"
              density="comfortable"
              hide-details
              class="collaborator-field"
            />
          </section>

          <!-- 底部操作 -->
          <footer class="settings-footer">
            <div class="save-note">
              <v-icon size="14">mdi-information-outline</v-icon>
              <span v-if="updatedAt">上次保存：{{ updatedAt }}</span>
              <span v-else>保存后会立即生效，不会影响历史 Telegram 消息。</span>
            </div>
            <div class="footer-actions">
              <v-btn color="error" variant="text" :loading="removingAll" @click="clearAll">
                <v-icon start>mdi-trash-can-outline</v-icon>
                清空我的配置
              </v-btn>
              <v-btn color="primary" size="large" prepend-icon="mdi-content-save" :loading="saving" @click="save">
                保存全部设置
              </v-btn>
            </div>
          </footer>
        </template>
      </div>
  </main>
</template>

<style scoped>
.channel-settings {
  min-height: 100dvh;
  padding: 18px;
  background: var(--mesh-bg);
  color: var(--text-primary);
  position: relative;
  overflow: hidden;
}

/* 装饰背景 */
.bg-mesh {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  pointer-events: none;
  z-index: 0;
}

.bg-mesh-1 {
  top: -15%;
  left: -10%;
  width: 480px;
  height: 480px;
  background: radial-gradient(circle, var(--mesh-glow-1) 0%, transparent 70%);
}

.bg-mesh-2 {
  bottom: -20%;
  right: -10%;
  width: 520px;
  height: 520px;
  background: radial-gradient(circle, var(--mesh-glow-2) 0%, transparent 70%);
}

.bg-grid {
  display: none;
}

.settings-shell {
  position: relative;
  z-index: 1;
  width: min(100%, 940px);
  margin: 0 auto;
  padding: 24px;
  background: var(--surface-card);
  border: 1px solid var(--glass-border-2);
  border-radius: var(--radius-dialog);
  box-shadow: var(--shadow-overlay);
  background-image: var(--surface-sheen);
  backdrop-filter: var(--surface-filter);
  -webkit-backdrop-filter: var(--surface-filter);
}

/* 顶部英雄区 */
.settings-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 20px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}

.hero-left {
  flex: 1;
  min-width: 0;
}

.hero-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--accent-soft);
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 8px;
}

.hero-title {
  font-size: 26px;
  font-weight: 800;
  line-height: 1.25;
  color: var(--text-primary);
  margin: 0 0 6px;
  letter-spacing: -0.02em;
}

.hero-desc {
  font-size: 13px;
  line-height: 1.55;
  color: var(--text-muted);
  margin: 0;
}

.hero-close {
  flex-shrink: 0;
}

/* 统计条 */
.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
  margin-bottom: 18px;
}

.stat-pill {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: all var(--transition);
}

.stat-pill:hover {
  border-color: var(--border-strong);
}

.stat-pill-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: var(--group-gradient);
  color: #fff;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.stat-pill-icon--success { background: linear-gradient(135deg, #10b981, #34d399); }
.stat-pill-icon--info { background: linear-gradient(135deg, #3b82f6, #60a5fa); }

.stat-pill-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.stat-pill-value {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}

.stat-pill-value--time {
  font-size: 12px;
  font-family: var(--font-mono);
  font-weight: 600;
}

.stat-pill-label {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.2;
}

/* 消息条 */
.settings-progress {
  margin-bottom: 14px;
}

.message-alert {
  margin-bottom: 14px;
}

/* 玻璃区块 */
.glass-section {
  padding: 20px;
  margin-bottom: 16px;
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-surface);
  box-shadow: var(--surface-shadow);
  position: relative;
  overflow: hidden;
}

.glass-section::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--group-gradient);
  opacity: 0.85;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}

.section-head-left {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.section-icon {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--group-gradient);
  color: #fff;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  box-shadow: 0 4px 12px var(--group-glow);
}

.section-icon--info { background: linear-gradient(135deg, #3b82f6, #60a5fa); box-shadow: 0 4px 12px rgba(59, 130, 246, 0.28); }
.section-icon--success { background: linear-gradient(135deg, #10b981, #34d399); box-shadow: 0 4px 12px rgba(16, 185, 129, 0.28); }

.section-head h2 {
  margin: 0 0 3px;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.3;
}

.section-head p {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-muted);
}

.section-head code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--accent-soft);
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--accent);
}

/* 频道卡片网格 */
.channel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}

.channel-card {
  padding: 16px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: all var(--transition);
}

.channel-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--surface-shadow);
}

.channel-card--default {
  border-color: var(--group-color);
  background: linear-gradient(135deg, var(--surface-card), var(--group-soft));
}

.channel-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.channel-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.channel-card-index {
  display: inline-grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.channel-card-title strong {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.muted {
  color: var(--text-muted);
  font-weight: 500;
}

.default-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  background: var(--group-gradient);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  border-radius: var(--radius-pill);
}

.channel-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.collaborator-field {
  margin-top: 12px;
}

/* 路由规则 */
.route-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.route-item {
  display: flex;
  gap: 12px;
  padding: 14px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: all var(--transition);
}

.route-item:hover {
  border-color: var(--border-strong);
}

.route-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  display: grid;
  place-items: center;
  color: #fff;
  flex-shrink: 0;
}

.route-icon--group { background: var(--group-gradient); }
.route-icon--success { background: linear-gradient(135deg, #10b981, #34d399); }
.route-icon--info { background: linear-gradient(135deg, #3b82f6, #60a5fa); }
.route-icon--warning { background: linear-gradient(135deg, #f59e0b, #fbbf24); }

.route-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.route-body label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

/* 底部 */
.settings-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}

.save-note {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}

.footer-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

/* 响应式 */
@media (max-width: 720px) {
  .channel-settings {
    padding: 0;
  }

  .settings-shell {
    padding: 68px 14px calc(24px + env(safe-area-inset-bottom, 0px));
    border-radius: 0;
    border: 0;
    box-shadow: none;
  }

  .settings-hero {
    position: relative;
    flex-direction: row;
    align-items: flex-start;
    padding-right: 42px;
  }

  .hero-close {
    position: absolute;
    top: -6px;
    right: -6px;
  }

  .field-grid,
  .route-grid {
    grid-template-columns: 1fr;
  }

  .channel-grid {
    grid-template-columns: 1fr;
  }

  .channel-card-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .channel-actions {
    width: 100%;
    justify-content: space-between;
  }

  .settings-footer {
    flex-direction: column;
    align-items: stretch;
  }

  .footer-actions {
    justify-content: stretch;
  }

  .footer-actions > .v-btn {
    flex: 1;
  }
}
</style>
