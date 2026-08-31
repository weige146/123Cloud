<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { channelOwnerApi, type MyChannelConfig, type MyChannelConfigUpdate } from "@/api";
import { useChannelConfigEditor } from "@/composables/useChannelConfigEditor";
import { useGlobalState } from "@/composables/useGlobalState";
import GlassCard from "@/components/GlassCard.vue";
import ChannelRoutingEditor from "@/components/ChannelRoutingEditor.vue";

const OWNER_STORAGE_KEY = "submissionRoutingOwner";
const { notifySuccess, notifyError, confirm } = useGlobalState();

const editor = useChannelConfigEditor();
const owners = ref<number[]>([]);
const selectedOwner = ref<number | null>(null);
const loading = ref(true);
const loadingConfig = ref(false);
const saving = ref(false);
const clearing = ref(false);
const error = ref("");
const notice = ref("");

const ownerItems = computed(() =>
  owners.value.map((id) => ({ title: String(id), value: id })),
);

function rememberOwner(userId: number) {
  try {
    localStorage.setItem(OWNER_STORAGE_KEY, String(userId));
  } catch { /* 隐私模式下忽略 */ }
}

function recallOwner(): number | null {
  try {
    const raw = Number(localStorage.getItem(OWNER_STORAGE_KEY) || "");
    return Number.isSafeInteger(raw) && raw > 0 ? raw : null;
  } catch {
    return null;
  }
}

async function loadOwnerConfig(userId: number) {
  loadingConfig.value = true;
  error.value = "";
  notice.value = "";
  try {
    const data = await channelOwnerApi.get(userId);
    editor.applyConfig(data.config);
  } catch (cause) {
    editor.resetToEmpty();
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loadingConfig.value = false;
  }
}

async function switchOwner(userId: number) {
  if (!userId || userId === selectedOwner.value) return;
  if (editor.isDirty) {
    const ok = await confirm("当前账号的修改还没有保存，切换后将丢失这些修改。确定切换账号吗？", "切换账号");
    if (!ok) return;
  }
  selectedOwner.value = userId;
  rememberOwner(userId);
  await loadOwnerConfig(userId);
}

async function save() {
  if (!selectedOwner.value) return;
  const payload = editor.buildPayload();
  if (!payload) {
    error.value = editor.state.validationMessage;
    return;
  }
  const update: MyChannelConfigUpdate = { channels: payload.channels, routing: payload.routing };
  saving.value = true;
  error.value = "";
  notice.value = "";
  try {
    const data = await channelOwnerApi.put(selectedOwner.value, update);
    editor.applyConfig(data.config);
    notice.value = `已保存账号 ${selectedOwner.value} 的频道路由配置，立即对投稿生效。`;
    notifySuccess("频道路由已保存");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    saving.value = false;
  }
}

async function clearOwnerConfig() {
  if (!selectedOwner.value) return;
  if (!(await confirm(`确定清空账号 ${selectedOwner.value} 的频道与路由配置吗？不会删除 Telegram 历史消息或历史发布记录。`, "清空该账号配置"))) return;
  clearing.value = true;
  error.value = "";
  notice.value = "";
  try {
    await channelOwnerApi.delete(selectedOwner.value);
    editor.resetToEmpty();
    notice.value = `已清空账号 ${selectedOwner.value} 的配置。`;
    notifySuccess("配置已清空");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    clearing.value = false;
  }
}

onMounted(async () => {
  loading.value = true;
  try {
    const data = await channelOwnerApi.owners();
    owners.value = data.owners || [];
    const remembered = recallOwner();
    const initial =
      (remembered && owners.value.includes(remembered) ? remembered : null) ||
      data.defaultOwnerUserId ||
      owners.value[0] ||
      null;
    selectedOwner.value = initial;
    if (initial) {
      rememberOwner(initial);
      await loadOwnerConfig(initial);
    } else {
      error.value = "还没有可管理的账号；请先在上方「权限与频道」里配置频道所有者 UID。";
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="routing-stack">
    <GlassCard
      title="选择账号"
      desc="按频道主 UID 管理各自的频道与路由；新账号需要先在「权限与频道」里加入频道所有者 UID，有历史配置的账号也会出现在列表里。"
      icon="mdi-account-switch-outline"
      :hover="false"
    >
      <template #actions>
        <v-select
          :model-value="selectedOwner"
          :items="ownerItems"
          label="频道主账号（Telegram UID）"
          placeholder="选择要配置的账号"
          :loading="loading"
          :disabled="loading || !owners.length"
          variant="outlined"
          density="compact"
          hide-details
          class="account-select"
          @update:model-value="switchOwner(Number($event))"
        />
      </template>
    </GlassCard>

    <GlassCard v-if="error" title="提示" icon="mdi-alert-circle-outline" :hover="false">
      <v-alert type="error" variant="tonal" closable @click:close="error = ''">{{ error }}</v-alert>
    </GlassCard>

    <GlassCard v-if="notice" title="提示" icon="mdi-check-circle-outline" :hover="false">
      <v-alert type="success" variant="tonal" closable @click:close="notice = ''">{{ notice }}</v-alert>
    </GlassCard>

    <GlassCard
      title="频道与自动路由"
      :desc="selectedOwner ? `正在编辑账号 ${selectedOwner} 的配置${editor.isDirty ? '（有未保存的修改）' : ''}。` : '先选择一个账号。'"
      icon="mdi-broadcast"
      :hover="false"
    >
      <v-progress-linear v-if="loadingConfig" indeterminate color="primary" class="config-progress" />
      <ChannelRoutingEditor v-if="selectedOwner && !loadingConfig" :editor="editor" />

      <div class="editor-footer">
        <div class="save-note">
          <v-icon size="14">mdi-information-outline</v-icon>
          <span v-if="editor.state.updatedAt">上次保存：{{ editor.state.updatedAt }}</span>
          <span v-else>保存后立即对投稿生效，不影响历史 Telegram 消息。</span>
        </div>
        <div class="footer-actions">
          <v-btn color="error" variant="text" :disabled="!selectedOwner" :loading="clearing" @click="clearOwnerConfig">
            <v-icon start>mdi-trash-can-outline</v-icon>
            清空该账号配置
          </v-btn>
          <v-btn color="primary" size="large" prepend-icon="mdi-content-save" :disabled="!selectedOwner || loadingConfig" :loading="saving" @click="save">
            保存配置
          </v-btn>
        </div>
      </div>
    </GlassCard>
  </div>
</template>

<style scoped>
.routing-stack {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.account-select {
  width: min(260px, 100%);
}

.config-progress {
  margin-bottom: 14px;
}

.editor-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 6px;
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

@media (max-width: 720px) {
  .editor-footer {
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
