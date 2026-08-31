// 频道配置编辑器：编辑状态与纯逻辑，不含任何 API 调用。
// ChannelSettingsView（Telegram 公开页）与 SubmissionRoutingPanel（桌面端投稿机器人页内的频道路由标签）共用，
// 保存动作由调用方注入：调用 buildPayload() 后自行发起请求，再用 applyConfig() 回填。
import { computed, reactive, ref } from "vue";
import type { Channel, Routing } from "@/api/types";

export type EditableChannel = Channel & { collaboratorText: string };

export interface ChannelConfigPayload {
  channels: Channel[];
  routing: Routing;
}

export interface ChannelConfigSource {
  channels?: Channel[];
  routing?: Routing;
  updatedAt?: string;
}

const ROUTE_KEYS = [
  "releaseGroupChannelId",
  "noReleaseGroupCompletedChannelId",
  "noReleaseGroupUpdatingChannelId",
  "fallbackChannelId",
] as const;

export function parseIds(text: string): number[] {
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

export function useChannelConfigEditor() {
  const state = reactive({
    channels: [] as EditableChannel[],
    routing: {
      releaseGroupChannelId: "",
      noReleaseGroupCompletedChannelId: "",
      noReleaseGroupUpdatingChannelId: "",
      fallbackChannelId: "",
      publicReleaseGroups: [],
    } as Routing,
    releaseGroupsText: "",
    updatedAt: "",
    validationMessage: "",
  });

  const channelOptions = computed(() => [
    { title: "不设置", value: "" },
    ...state.channels.map((channel) => ({ title: String(channel.title ?? "").trim() || "未命名频道", value: channel.id })),
  ]);

  const enabledChannelCount = computed(() => state.channels.filter((c) => c.enabled !== false).length);

  function makeChannelId(): string {
    return `channel_${Date.now()}_${state.channels.length + 1}`;
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

  // 当前编辑内容的稳定快照，用于 isDirty 判断与基线对比。
  function currentShape() {
    return {
      channels: state.channels.map((channel) => ({
        id: channel.id,
        title: String(channel.title ?? "").trim(),
        chatId: String(channel.chatId ?? "").trim(),
        role: channel.role,
        enabled: channel.enabled !== false,
        isDefault: Boolean(channel.isDefault),
        collaboratorText: channel.collaboratorText,
      })),
      routing: {
        releaseGroupChannelId: state.routing.releaseGroupChannelId || "",
        noReleaseGroupCompletedChannelId: state.routing.noReleaseGroupCompletedChannelId || "",
        noReleaseGroupUpdatingChannelId: state.routing.noReleaseGroupUpdatingChannelId || "",
        fallbackChannelId: state.routing.fallbackChannelId || "",
        releaseGroupsText: state.releaseGroupsText,
      },
    };
  }

  const baseline = ref(JSON.stringify(currentShape()));
  const isDirty = computed(() => JSON.stringify(currentShape()) !== baseline.value);

  function markClean() {
    baseline.value = JSON.stringify(currentShape());
  }

  function applyConfig(config: ChannelConfigSource) {
    state.channels = (config.channels || []).map(editableChannel);
    if (!state.channels.length) addChannel();
    const source = config.routing || {};
    state.routing.releaseGroupChannelId = String(source.releaseGroupChannelId || "");
    state.routing.noReleaseGroupCompletedChannelId = String(source.noReleaseGroupCompletedChannelId || "");
    state.routing.noReleaseGroupUpdatingChannelId = String(source.noReleaseGroupUpdatingChannelId || "");
    state.routing.fallbackChannelId = String(source.fallbackChannelId || "");
    state.releaseGroupsText = (source.publicReleaseGroups || []).join("\n");
    state.updatedAt = config.updatedAt || "";
    state.validationMessage = "";
    markClean();
  }

  function resetToEmpty() {
    state.channels = [];
    state.routing = {
      releaseGroupChannelId: "",
      noReleaseGroupCompletedChannelId: "",
      noReleaseGroupUpdatingChannelId: "",
      fallbackChannelId: "",
      publicReleaseGroups: [],
    };
    state.releaseGroupsText = "";
    state.updatedAt = "";
    state.validationMessage = "";
    addChannel();
    markClean();
  }

  function addChannel() {
    state.channels.push({
      id: makeChannelId(),
      title: "",
      chatId: "",
      role: "private",
      enabled: true,
      isDefault: state.channels.length === 0,
      collaboratorText: "",
    });
  }

  function setDefault(target: EditableChannel, enabled: boolean) {
    if (!enabled) {
      target.isDefault = false;
      return;
    }
    state.channels.forEach((channel) => { channel.isDefault = channel === target; });
  }

  function removeChannel(target: EditableChannel) {
    if (state.channels.length === 1) {
      state.validationMessage = "请至少保留一个频道卡片。";
      return;
    }
    const index = state.channels.indexOf(target);
    if (index < 0) return;
    const deletedId = target.id;
    const wasDefault = Boolean(target.isDefault);
    state.channels.splice(index, 1);
    ROUTE_KEYS.forEach((key) => {
      if (state.routing[key] === deletedId) state.routing[key] = "";
    });
    if (wasDefault) state.channels[0].isDefault = true;
  }

  // 校验并组装可提交的配置；校验失败时写入 state.validationMessage 并返回 null。
  function buildPayload(): ChannelConfigPayload | null {
    state.validationMessage = "";
    const ids = new Set<string>();
    const result: Channel[] = [];
    for (const channel of state.channels) {
      const title = String(channel.title ?? "").trim();
      const chatId = String(channel.chatId ?? "").trim();
      if (!title || !chatId) {
        state.validationMessage = "每个频道都需要填写“显示名称”和“频道 Chat ID”。";
        return null;
      }
      if (ids.has(channel.id)) {
        state.validationMessage = "频道保存失败：检测到重复频道。";
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
    return {
      channels: result,
      routing: {
        releaseGroupChannelId: selectedRoute(state.routing.releaseGroupChannelId),
        noReleaseGroupCompletedChannelId: selectedRoute(state.routing.noReleaseGroupCompletedChannelId),
        noReleaseGroupUpdatingChannelId: selectedRoute(state.routing.noReleaseGroupUpdatingChannelId),
        fallbackChannelId: selectedRoute(state.routing.fallbackChannelId),
        publicReleaseGroups: parseLines(state.releaseGroupsText),
      },
    };
  }

  // 用 reactive 包装返回值：跨组件透传时模板里 channelOptions/isDirty 等 computed 自动解包。
  return reactive({
    state,
    channelOptions,
    enabledChannelCount,
    isDirty,
    applyConfig,
    resetToEmpty,
    addChannel,
    setDefault,
    removeChannel,
    buildPayload,
    markClean,
  });
}

export type ChannelConfigEditor = ReturnType<typeof useChannelConfigEditor>;
