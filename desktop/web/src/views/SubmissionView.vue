<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useGlobalState } from "@/composables/useGlobalState";
import { submissionApi } from "@/api";
import type { SubmissionConfig, SubmissionDraft } from "@/api/types";
import { parseIds } from "@/utils/format";
import PageHero from "@/components/PageHero.vue";
import GlassCard from "@/components/GlassCard.vue";
import FormGrid from "@/components/FormGrid.vue";
import FormField from "@/components/FormField.vue";
import SegmentedTabs from "@/components/SegmentedTabs.vue";
import SubmissionRoutingPanel from "@/components/SubmissionRoutingPanel.vue";

const route = useRoute();
const router = useRouter();
const { state, writeSubmissionConfig, notifySuccess, notifyError, confirm, loadStatus } = useGlobalState();

// 「机器人」= Bot 连接/权限/草稿；「频道路由」= 各频道主账号的分发规则
type SubmissionTab = "bot" | "routing";
const tabsList = [
  { key: "bot", label: "机器人", icon: "mdi-robot" },
  { key: "routing", label: "频道路由", icon: "mdi-routes" },
];

function tabFromQuery(): SubmissionTab {
  return route.query.tab === "routing" ? "routing" : "bot";
}
const tab = ref<SubmissionTab>(tabFromQuery());
watch(
  () => route.query.tab,
  () => {
    const next = tabFromQuery();
    if (tab.value !== next) tab.value = next;
  },
);
watch(tab, (next) => {
  const target = next === "routing" ? { query: { tab: "routing" } } : { query: {} };
  if (route.query.tab !== (next === "routing" ? "routing" : undefined)) {
    router.replace({ path: "/admin/submission", ...target });
  }
});

const form = reactive({
  botToken: "",
  tmdbToken: "",
  tmdbLanguage: "zh-CN",
  tgApiId: "",
  tgApiHash: "",
  tgSession: "",
  telegramAdminUserIds: "",
  channelOwnerUserIds: "",
});

const statusText = ref("投稿配置未加载");
const drafts = ref<SubmissionDraft[]>([]);
const loadingDrafts = ref(false);
const saving = ref(false);
const saveFeedback = ref("");
const saveFailed = ref(false);

const submissionStatus = computed(() => state.submissionStatus);

function syncFromConfig() {
  const c = state.submissionConfig;
  if (!c) return;
  form.botToken = String(c.botToken ?? "");
  form.tmdbToken = String(c.tmdbToken ?? "");
  form.tmdbLanguage = String(c.tmdbLanguage ?? "") || "zh-CN";
  form.tgApiId = String(c.telegramApi?.apiId ?? "");
  form.tgApiHash = String(c.telegramApi?.apiHash ?? "");
  form.tgSession = String(c.telegramApi?.session ?? "");
  form.telegramAdminUserIds = (c.telegramAdminUserIds || c.allowedUserIds || []).join("\n");
  form.channelOwnerUserIds = (c.channelOwnerUserIds || c.allowedUserIds || []).join("\n");
  const s = state.submissionStatus;
  if (s) {
    statusText.value = [
      s.botConfigured ? "Bot 已配置" : "Bot 未配置",
      s.allowedUserCount ? `Bot 管理员 ${s.allowedUserCount} 个` : "未配置 Bot 管理员",
      "Bot 轮询接收消息",
      s.tmdbConfigured ? "TMDB 已配置" : "TMDB 未配置",
      s.telegramApiConfigured ? "TG API 已配置" : "TG API 未配置",
      s.draftCount ? `草稿 ${s.draftCount} 条` : "暂无草稿",
    ].join(" · ");
  }
}

watch(
  () => state.loaded,
  (loaded) => {
    if (loaded) syncFromConfig();
  },
  { immediate: true }
);

async function refreshDrafts() {
  loadingDrafts.value = true;
  try {
    const data = await submissionApi.drafts();
    drafts.value = data.drafts || [];
    notifySuccess("投稿草稿已刷新");
  } catch (error) {
    notifyError(`投稿草稿刷新失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    loadingDrafts.value = false;
  }
}

async function clearDrafts() {
  if (!(await confirm("清空所有投稿草稿？"))) return;
  try {
    await submissionApi.clearDrafts();
    await refreshDrafts();
    notifySuccess("投稿草稿已清空");
  } catch (error) {
    notifyError(`清空草稿失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function copyDraft(draft: SubmissionDraft) {
  try {
    await navigator.clipboard.writeText(String(draft.text || ""));
    notifySuccess("草稿内容已复制");
  } catch (error) {
    notifyError(`复制失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function submitDraft(draft: SubmissionDraft) {
  try {
    await submissionApi.submitDraft(draft.id);
    await refreshDrafts();
    notifySuccess("草稿已重新投稿");
  } catch (error) {
    notifyError(`重新投稿失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function deleteDraft(draft: SubmissionDraft) {
  if (!(await confirm("删除这条投稿草稿？"))) return;
  try {
    await submissionApi.deleteDraft(draft.id);
    await refreshDrafts();
    notifySuccess("投稿草稿已删除");
  } catch (error) {
    notifyError(`删除草稿失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function save() {
  if (!state.loaded) {
    saveFailed.value = true;
    saveFeedback.value = "投稿配置仍在读取中，请稍候再保存。";
    notifyError(saveFeedback.value);
    return;
  }
  saving.value = true;
  saveFailed.value = false;
  saveFeedback.value = "正在保存投稿配置…";
  try {
    // This view owns only the Bot connection and authorization fields.  Send a
    // compact partial update instead of serializing all recognition rules and
    // templates; the server merges it with the persisted configuration.
    const next: SubmissionConfig = {
      botToken: String(form.botToken ?? "").trim(),
      tmdbToken: String(form.tmdbToken ?? "").trim(),
      tmdbLanguage: String(form.tmdbLanguage ?? "").trim() || "zh-CN",
      telegramApi: {
        apiId: String(form.tgApiId ?? "").trim(),
        apiHash: String(form.tgApiHash ?? "").trim(),
        session: String(form.tgSession ?? "").trim(),
      },
      telegramAdminUserIds: parseIds(form.telegramAdminUserIds),
      channelOwnerUserIds: parseIds(form.channelOwnerUserIds),
    };
    await writeSubmissionConfig(next);
    // Reflect the server's persisted value immediately.  This also makes a
    // backend-side normalization visible without requiring a page refresh.
    syncFromConfig();
    saveFeedback.value = "投稿配置已保存。";
    notifySuccess(saveFeedback.value);
  } catch (error) {
    saveFailed.value = true;
    saveFeedback.value = `投稿配置保存失败：${error instanceof Error ? error.message : String(error)}`;
    notifyError(saveFeedback.value);
  } finally {
    saving.value = false;
  }
}

async function testBot() {
  try {
    const data = await submissionApi.testBot(String(form.botToken ?? "").trim());
    notifySuccess(data.message || "Bot 测试成功");
  } catch (error) {
    notifyError(`Bot 测试失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function testTgApi() {
  const session = String(form.tgSession ?? "").trim();
  if (!session) {
    notifyError("请先获取或填写 TG 用户 Session");
    return;
  }
  tgMessage.value = "正在连接 Telegram…";
  try {
    const data = await submissionApi.testTelegramApi({
      apiId: String(form.tgApiId ?? "").trim(),
      apiHash: String(form.tgApiHash ?? "").trim(),
      session,
    });
    tgMessage.value = data.message || "TG API 测试成功";
    notifySuccess(tgMessage.value);
  } catch (error) {
    tgMessage.value = `TG API 测试失败：${error instanceof Error ? error.message : String(error)}`;
    notifyError(tgMessage.value);
  }
}

// ---- 获取 TG 用户 Session：手机号 → 验证码 → （可选）两步验证密码 ----
type TgStage = "idle" | "code" | "password";
const tgLogin = reactive({ phone: "", code: "", password: "" });
const tgStage = ref<TgStage>("idle");
const tgBusy = ref(false);
const tgMessage = ref("");
const tgLoginId = ref("");

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function resetTelegramLogin() {
  const loginId = tgLoginId.value;
  tgLoginId.value = "";
  tgStage.value = "idle";
  tgLogin.code = "";
  tgLogin.password = "";
  if (loginId) {
    try {
      await submissionApi.cancelTelegramSession(loginId);
    } catch {
      // 服务端会话可能已经过期，取消失败不用打断用户
    }
  }
}

async function requestTelegramCode() {
  const apiId = String(form.tgApiId ?? "").trim();
  const apiHash = String(form.tgApiHash ?? "").trim();
  if (!apiId || !apiHash) {
    notifyError("请先填写 TG API ID 和 API Hash");
    return;
  }
  if (!String(tgLogin.phone ?? "").trim()) {
    notifyError("请填写手机号（带国际区号，例如 +8613800000000）");
    return;
  }
  tgBusy.value = true;
  tgMessage.value = "正在发送验证码…";
  try {
    if (tgLoginId.value) {
      await submissionApi.cancelTelegramSession(tgLoginId.value).catch(() => undefined);
      tgLoginId.value = "";
    }
    const data = await submissionApi.startTelegramSession({
      apiId,
      apiHash,
      phone: String(tgLogin.phone ?? "").trim(),
    });
    tgLoginId.value = data.loginId || "";
    tgLogin.code = "";
    tgLogin.password = "";
    tgStage.value = "code";
    tgMessage.value = data.message || "验证码已发送，请查收";
    notifySuccess("验证码已发送");
  } catch (error) {
    tgMessage.value = `获取验证码失败：${errorMessage(error)}`;
    notifyError(tgMessage.value);
  } finally {
    tgBusy.value = false;
  }
}

async function verifyTelegramLogin() {
  if (!tgLoginId.value) {
    notifyError("请先点击「获取验证码」");
    return;
  }
  if (!String(tgLogin.code ?? "").trim()) {
    notifyError("请填写 Telegram 发来的验证码");
    return;
  }
  tgBusy.value = true;
  tgMessage.value = "正在登录…";
  try {
    const data = await submissionApi.verifyTelegramSession({
      loginId: tgLoginId.value,
      code: String(tgLogin.code ?? "").trim(),
      password: String(tgLogin.password ?? "").trim(),
    });
    if (data.needPassword) {
      tgStage.value = "password";
      tgMessage.value = data.message || "该账号开启了两步验证，请输入云密码";
      return;
    }
    if (data.session) form.tgSession = data.session;
    if (data.apiId) form.tgApiId = String(data.apiId);
    if (data.apiHash) form.tgApiHash = String(data.apiHash);
    tgLoginId.value = "";
    tgStage.value = "idle";
    tgLogin.code = "";
    tgLogin.password = "";
    tgMessage.value = data.message || "Session 已获取并保存";
    // 后端已经写库，这里回读一次让顶部状态与配置同步；
    // 放在 loadStatus 之后再提示，避免被"后端连接正常"的 toast 顶掉
    await loadStatus();
    notifySuccess(tgMessage.value);
  } catch (error) {
    tgMessage.value = `登录失败：${errorMessage(error)}`;
    notifyError(tgMessage.value);
  } finally {
    tgBusy.value = false;
  }
}

onMounted(() => {
  refreshDrafts();
});
</script>

<template>
  <div class="page fade-rise" data-group="dashboard">
    <PageHero
      group="dashboard"
      icon="mdi-robot"
      title="投稿机器人"
      desc="分享和秒传链接会自动提交到投稿机器人；Bot 配置与频道路由都在这里。"
    >
      <template #status>
        <span class="chip-status" :data-tone="submissionStatus?.botConfigured ? 'success' : 'warning'">
          <v-icon size="14">{{ submissionStatus?.botConfigured ? 'mdi-check-circle' : 'mdi-alert-circle' }}</v-icon>
          Bot {{ submissionStatus?.botConfigured ? '已配置' : '未配置' }}
        </span>
        <span class="chip-status" :data-tone="submissionStatus?.tmdbConfigured ? 'success' : 'neutral'">
          <v-icon size="14">mdi-database-search</v-icon>
          TMDB {{ submissionStatus?.tmdbConfigured ? '已配置' : '未配置' }}
        </span>
        <span class="chip-status" :data-tone="submissionStatus?.telegramApiConfigured ? 'success' : 'neutral'">
          <v-icon size="14">mdi-send</v-icon>
          TG API {{ submissionStatus?.telegramApiConfigured ? '已配置' : '未配置' }}
        </span>
        <span class="chip-status" data-tone="info">
          <v-icon size="14">mdi-file-document-multiple</v-icon>
          草稿 {{ submissionStatus?.draftCount || 0 }}
        </span>
      </template>
      <template v-if="tab === 'bot'" #actions>
        <v-btn variant="text" @click="testBot">
          <v-icon start>mdi-connection</v-icon>
          测试 Bot
        </v-btn>
        <v-btn color="primary" :loading="saving" @click="save">
          <v-icon start>mdi-content-save</v-icon>
          {{ state.loaded ? "保存投稿配置" : "正在读取配置…" }}
        </v-btn>
      </template>
    </PageHero>

    <SegmentedTabs v-model="tab" :tabs="tabsList" />

    <div v-show="tab === 'bot'" class="section-grid">
      <GlassCard
        accent="group"
        icon="mdi-connection"
        title="连接配置"
        desc="Bot Token、TMDB 与 TG API 凭据，用于投稿生成与旧帖清理。"
        :span="2"
      >
        <template #actions>
          <v-btn variant="text" @click="testBot">测试 Bot</v-btn>
          <v-btn variant="text" @click="testTgApi">测试 TG API</v-btn>
          <v-btn color="primary" :loading="saving" @click="save">
            {{ state.loaded ? "保存" : "读取中…" }}
          </v-btn>
        </template>
        <FormGrid>
          <FormField label="Bot Token" hint="Telegram Bot Token，机器人的身份标识。">
            <v-text-field
              v-model="form.botToken"
              type="password"
              autocomplete="new-password"
              placeholder="Telegram Bot Token"
              variant="outlined"
              density="compact"
              hide-details
            />
          </FormField>
          <FormField label="TMDB Token">
            <v-text-field
              v-model="form.tmdbToken"
              type="password"
              autocomplete="new-password"
              placeholder="TMDB API Token"
              variant="outlined"
              density="compact"
              hide-details
            />
          </FormField>
          <FormField label="TMDB 语言">
            <v-text-field
              v-model="form.tmdbLanguage"
              autocomplete="off"
              placeholder="zh-CN"
              variant="outlined"
              density="compact"
              hide-details
            />
          </FormField>
          <FormField label="TG API ID" hint="用于频道旧帖清理。">
            <v-text-field
              v-model="form.tgApiId"
              autocomplete="off"
              placeholder="用于频道旧帖清理"
              variant="outlined"
              density="compact"
              hide-details
            />
          </FormField>
          <FormField label="TG API Hash">
            <v-text-field
              v-model="form.tgApiHash"
              type="password"
              autocomplete="new-password"
              placeholder="Telegram API Hash"
              variant="outlined"
              density="compact"
              hide-details
            />
          </FormField>
          <FormField label="TG 用户 Session" :full="true" hint="Telegram 用户 Session（StringSession 格式），用于发布后清理频道旧帖。">
            <v-textarea
              v-model="form.tgSession"
              :rows="3"
              placeholder="Telegram 用户 Session（StringSession 格式）"
              variant="outlined"
              density="compact"
              hide-details
            />
          </FormField>
          <FormField
            :full="true"
            label="获取 TG 用户 Session"
            hint="在客户端里直接登录 Telegram 账号拿 Session：填手机号 → 收验证码 → 登录成功自动填入上方并保存，不用再去别处跑脚本粘贴。"
          >
            <div class="tg-login">
              <div class="tg-login-row">
                <v-text-field
                  v-model="tgLogin.phone"
                  placeholder="+8613800000000"
                  variant="outlined"
                  density="compact"
                  hide-details
                  :disabled="tgBusy || tgStage !== 'idle'"
                  @keyup.enter="requestTelegramCode"
                />
                <v-btn
                  v-if="tgStage === 'idle'"
                  color="primary"
                  variant="tonal"
                  :loading="tgBusy"
                  @click="requestTelegramCode"
                >
                  <v-icon start>mdi-message-outline</v-icon>
                  获取验证码
                </v-btn>
                <v-btn v-else variant="text" :disabled="tgBusy" @click="resetTelegramLogin">取消登录</v-btn>
              </div>
              <div v-if="tgStage !== 'idle'" class="tg-login-row">
                <v-text-field
                  v-model="tgLogin.code"
                  placeholder="Telegram 发来的验证码"
                  variant="outlined"
                  density="compact"
                  hide-details
                  :disabled="tgBusy"
                  @keyup.enter="verifyTelegramLogin"
                />
                <v-btn color="primary" :loading="tgBusy" @click="verifyTelegramLogin">
                  <v-icon start>mdi-login</v-icon>
                  登录并生成 Session
                </v-btn>
              </div>
              <v-text-field
                v-if="tgStage === 'password'"
                v-model="tgLogin.password"
                type="password"
                autocomplete="new-password"
                placeholder="两步验证密码（云密码）"
                variant="outlined"
                density="compact"
                hide-details
                :disabled="tgBusy"
                @keyup.enter="verifyTelegramLogin"
              />
              <div v-if="tgMessage" class="tg-login-message">{{ tgMessage }}</div>
            </div>
          </FormField>
        </FormGrid>
        <div class="status-line mono-value">{{ statusText }}</div>
        <v-alert v-if="saveFeedback" :type="saveFailed ? 'error' : 'success'" variant="tonal" density="compact" class="save-feedback">
          {{ saveFeedback }}
        </v-alert>
      </GlassCard>

      <GlassCard
        accent="info"
        icon="mdi-account-multiple-outline"
        title="权限与频道"
        desc="可投稿的 Bot 管理员与频道所有者 UID，每行一个。"
      >
        <FormField label="Bot 管理员 Telegram UID" hint="可使用搬运、115 等非投稿功能；每行一个 UID。">
          <v-textarea
            v-model="form.telegramAdminUserIds"
            :rows="4"
            placeholder="可使用搬运、115 等非投稿功能；每行一个 UID"
            variant="outlined"
            density="compact"
            hide-details
          />
        </FormField>
        <FormField label="频道所有者 Telegram UID" hint="可在「频道路由」标签页管理其频道与路由；每行一个 UID。">
          <v-textarea
            v-model="form.channelOwnerUserIds"
            :rows="4"
            placeholder="可在「频道路由」标签页管理其频道与路由；每行一个 UID"
            variant="outlined"
            density="compact"
            hide-details
          />
        </FormField>
        <p class="routing-link-row">
          <button type="button" class="routing-link" @click="tab = 'routing'">
            去配置频道路由 →
          </button>
        </p>
      </GlassCard>

      <GlassCard
        accent="group"
        icon="mdi-file-document-edit-outline"
        title="投稿草稿"
        desc="机器人生成的草稿会保存在数据库，发布后会清理对应草稿。"
        :span="2"
      >
        <template #actions>
          <v-btn variant="text" :loading="loadingDrafts" @click="refreshDrafts">刷新草稿</v-btn>
          <v-btn variant="outlined" color="error" @click="clearDrafts">清空草稿</v-btn>
        </template>
        <div v-if="!drafts.length" class="empty-state">
          <v-icon size="40">mdi-file-document-multiple-outline</v-icon>
          <p>暂无投稿草稿</p>
        </div>
        <div v-else class="draft-list">
          <div v-for="draft in drafts" :key="draft.id" class="draft-item">
            <div class="draft-item-head">
              <div class="draft-item-title-wrap">
                <strong class="draft-item-title">{{ draft.sourceLabel || "投稿草稿" }}</strong>
                <div class="draft-item-meta">
                  <span class="chip-status" :data-tone="draft.sent ? 'success' : 'info'">
                    {{ draft.sent ? "已投稿" : "未投稿" }}
                  </span>
                  <span class="muted-line">{{ draft.linkCount || 0 }} 个链接</span>
                  <span v-if="draft.channelTitle" class="muted-line">路由 {{ draft.channelTitle }}</span>
                  <span class="muted-line">{{ draft.createdAt || "" }}</span>
                </div>
              </div>
              <div class="draft-item-actions">
                <v-btn size="small" variant="text" prepend-icon="mdi-content-copy" @click="copyDraft(draft)">复制</v-btn>
                <v-btn size="small" variant="outlined" prepend-icon="mdi-send" @click="submitDraft(draft)">重新投稿</v-btn>
                <v-btn size="small" variant="outlined" color="error" prepend-icon="mdi-delete-outline" @click="deleteDraft(draft)">删除</v-btn>
              </div>
            </div>
            <pre class="draft-preview">{{ draft.caption || draft.text || "" }}</pre>
          </div>
        </div>
      </GlassCard>
    </div>

    <SubmissionRoutingPanel v-show="tab === 'routing'" />
  </div>
</template>

<style scoped>
.status-line {
  font-size: 11.5px;
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--glass-bg-3);
  border: 1px solid var(--glass-border-3);
  color: var(--text-secondary);
  line-height: 1.6;
  word-break: break-all;
}

.tg-login {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tg-login-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.tg-login-row > :first-child {
  flex: 1 1 220px;
  min-width: 0;
}

.tg-login-message {
  font-size: 11.5px;
  color: var(--text-secondary);
  line-height: 1.6;
  word-break: break-all;
}

.save-feedback {
  margin-top: 12px;
}

.draft-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.draft-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: var(--glass-bg-3);
  backdrop-filter: blur(var(--glass-blur-3)) saturate(var(--glass-saturate));
  -webkit-backdrop-filter: blur(var(--glass-blur-3)) saturate(var(--glass-saturate));
  border: 1px solid var(--glass-border-3);
  border-left: 3px solid var(--group-color);
  border-radius: var(--radius-md);
  transition: background-color var(--transition), border-color var(--transition);
}

.draft-item:hover {
  background: var(--glass-bg-2);
  border-color: var(--group-color);
}

.draft-item-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  flex-wrap: wrap;
}

.draft-item-title-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.draft-item-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.draft-item-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.muted-line {
  font-size: 11px;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.draft-item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.draft-preview {
  margin: 0;
  padding: 10px 12px;
  background: var(--glass-bg-2);
  border: 1px solid var(--glass-border-3);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 144px;
  overflow: auto;
}

.chip-status[data-tone="neutral"] {
  background: var(--glass-bg-3);
  color: var(--text-muted);
  border-color: var(--glass-border-3);
}

.routing-link-row {
  margin: 10px 2px 0;
  font-size: 12.5px;
}

.routing-link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent);
  font: inherit;
  font-size: 12.5px;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
}

.routing-link:hover {
  text-decoration: underline;
}

@media (max-width: 760px) {
  .draft-item-head {
    flex-direction: column;
    align-items: stretch;
  }
  .draft-item-actions {
    justify-content: flex-end;
  }
}
</style>
