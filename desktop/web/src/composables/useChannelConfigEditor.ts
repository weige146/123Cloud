// 频道配置编辑器：编辑状态与纯逻辑，不含任何 API 调用。
// ChannelRoutingEditor（桌面端投稿机器人页内的频道路由标签）与 Telegram 公开设置页共用，
// 保存动作由调用方注入：调用 buildPayload() 后自行发起请求，再用 applyConfig() 回填。
//
// 路由为 v2 有序规则列表（首条命中即停），语义与后端 app/submission_routing.py 一致；
// 旧三槽配置在这里也有一份等价迁移（后端读接口已迁移，此函数兜底本地迁移与老数据预览）。
import { computed, reactive, ref } from "vue";
import type { Channel, Routing, RoutingRule } from "@/api/types";

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

function cleanChannelId(value: unknown): string {
  return String(value ?? "").trim();
}

// 旧三槽 → v2 规则（与后端 migrate_v1_routing 同顺序同语义）
export function migrateLegacyRouting(source: Routing): Routing {
  const groups = parseLines((source.publicReleaseGroups || []).join("\n"));
  const completedId = cleanChannelId(source.noReleaseGroupCompletedChannelId);
  const updatingId = cleanChannelId(source.noReleaseGroupUpdatingChannelId);
  const releaseGroupId = cleanChannelId(source.releaseGroupChannelId);
  const rules: RoutingRule[] = [];
  if (releaseGroupId && groups.length) {
    rules.push({ id: "migrated-release-group", name: "发布组不在白名单", enabled: true, when: { releaseGroupState: ["detected"], releaseGroupWhitelist: ["notIn"] }, channelId: releaseGroupId });
  }
  if (completedId) {
    rules.push({ id: "migrated-completed", name: "完结内容", enabled: true, when: { completion: ["completed"] }, channelId: completedId });
  }
  if (updatingId) {
    rules.push({ id: "migrated-updating", name: "连载内容", enabled: true, when: { completion: ["updating"] }, channelId: updatingId });
  }
  const fallback = cleanChannelId(source.fallbackChannelId) || releaseGroupId;
  if (fallback) {
    rules.push({ id: "migrated-fallback", name: "默认投递", enabled: true, when: {}, channelId: fallback });
  }
  return { version: 2, rules, fallbackChannelId: cleanChannelId(source.fallbackChannelId), publicReleaseGroups: groups };
}

export function normalizeRoutingToV2(source: Routing | undefined): Routing {
  const raw = source || {};
  if (Array.isArray(raw.rules) && raw.rules.some((rule) => rule && (rule.channelId || rule.when))) {
    return {
      version: 2,
      rules: raw.rules.map((rule, index) => ({
        id: String(rule.id || `rule_${index + 1}`),
        name: String(rule.name || ""),
        enabled: rule.enabled !== false,
        when: (rule.when || {}) as RoutingRule["when"],
        channelId: cleanChannelId(rule.channelId),
      })),
      fallbackChannelId: cleanChannelId(raw.fallbackChannelId),
      publicReleaseGroups: parseLines((raw.publicReleaseGroups || []).join("\n")),
    };
  }
  return migrateLegacyRouting(raw);
}

// v2 规则回推 v1 影子槽（迁移产物能还原；用户自定义规则映射不到就留空，v2 规则始终是真源）
function legacyShadowKeys(routing: Routing): Pick<Routing, "releaseGroupChannelId" | "noReleaseGroupCompletedChannelId" | "noReleaseGroupUpdatingChannelId"> {
  const byId = new Map((routing.rules || []).map((rule) => [rule.id, rule]));
  const pick = (id: string) => {
    const rule = byId.get(id);
    const when = (rule?.when || {}) as Record<string, unknown>;
    const sameArray = (value: unknown, wanted: string[]) => Array.isArray(value) && value.length === wanted.length && wanted.every((item) => (value as string[]).includes(item));
    if (!rule) return "";
    if (id === "migrated-release-group" && sameArray(when.releaseGroupState, ["detected"]) && sameArray(when.releaseGroupWhitelist, ["notIn"])) return rule.channelId;
    if ((id === "migrated-completed" || id === "migrated-updating") && Object.keys(when).length === 1) return rule.channelId;
    if (id === "migrated-fallback" && Object.keys(when).length === 0) return rule.channelId;
    return "";
  };
  return {
    releaseGroupChannelId: pick("migrated-release-group"),
    noReleaseGroupCompletedChannelId: pick("migrated-completed"),
    noReleaseGroupUpdatingChannelId: pick("migrated-updating"),
  };
}

export function useChannelConfigEditor() {
  const state = reactive({
    channels: [] as EditableChannel[],
    routing: { version: 2, rules: [], fallbackChannelId: "", publicReleaseGroups: [] } as Routing,
    releaseGroupsText: "",
    updatedAt: "",
    validationMessage: "",
  });

  const channelOptions = computed(() => [
    { title: "不设置", value: "" },
    ...state.channels.map((channel) => ({ title: String(channel.title ?? "").trim() || "未命名频道", value: channel.id })),
  ]);

  const enabledChannelOptions = computed(() => [
    { title: "不设置", value: "" },
    ...state.channels.filter((channel) => channel.enabled !== false).map((channel) => ({ title: String(channel.title ?? "").trim() || "未命名频道", value: channel.id })),
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
      cleanupOldPosts: value.cleanupOldPosts !== false,
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
        cleanupOldPosts: channel.cleanupOldPosts !== false,
        isDefault: Boolean(channel.isDefault),
        collaboratorText: channel.collaboratorText,
      })),
      routing: {
        rules: (state.routing.rules || []).map((rule) => ({
          id: rule.id,
          name: rule.name || "",
          enabled: rule.enabled !== false,
          when: rule.when || {},
          channelId: rule.channelId || "",
        })),
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
    const routing = normalizeRoutingToV2(config.routing);
    state.routing.version = 2;
    state.routing.rules = routing.rules || [];
    state.routing.fallbackChannelId = routing.fallbackChannelId || "";
    state.releaseGroupsText = (routing.publicReleaseGroups || []).join("\n");
    state.updatedAt = config.updatedAt || "";
    state.validationMessage = "";
    markClean();
  }

  function resetToEmpty() {
    state.channels = [];
    state.routing = { version: 2, rules: [], fallbackChannelId: "", publicReleaseGroups: [] };
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
      cleanupOldPosts: true,
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
    if (state.routing.fallbackChannelId === deletedId) state.routing.fallbackChannelId = "";
    (state.routing.rules || []).forEach((rule) => {
      if (rule.channelId === deletedId) rule.channelId = "";
    });
    if (wasDefault) state.channels[0].isDefault = true;
  }

  // ===== 规则列表编辑 =====

  function addRule(preset?: Partial<RoutingRule>): string {
    const id = `rule_${Date.now()}_${(state.routing.rules || []).length + 1}`;
    const rule: RoutingRule = {
      id,
      name: preset?.name || "",
      enabled: true,
      when: preset?.when ? JSON.parse(JSON.stringify(preset.when)) : {},
      channelId: preset?.channelId || "",
    };
    state.routing.rules = [...(state.routing.rules || []), rule];
    return id;
  }

  function removeRule(id: string) {
    state.routing.rules = (state.routing.rules || []).filter((rule) => rule.id !== id);
  }

  function moveRule(index: number, direction: -1 | 1) {
    const rules = [...(state.routing.rules || [])];
    const target = index + direction;
    if (index < 0 || target < 0 || target >= rules.length) return;
    [rules[index], rules[target]] = [rules[target], rules[index]];
    state.routing.rules = rules;
  }

  function setConditionValues(rule: RoutingRule, key: string, values: string[]) {
    if (!rule.when) rule.when = {};
    const conditions = rule.when as Record<string, unknown>;
    const cleaned = values.map((value) => String(value ?? "").trim()).filter(Boolean);
    if (key === "tmdbId") {
      if (cleaned.includes("required")) conditions.tmdbId = "required";
      else if (cleaned.length) conditions.tmdbId = cleaned.map((value) => Number(value)).filter((value) => Number.isSafeInteger(value) && value > 0);
      else delete conditions.tmdbId;
      rule.when = { ...conditions };
      return;
    }
    if (cleaned.length) conditions[key] = cleaned;
    else delete conditions[key];
    rule.when = { ...conditions };
  }

  function conditionValues(rule: RoutingRule, key: string): string[] {
    const value = (rule.when as Record<string, unknown> | undefined)?.[key];
    if (key === "tmdbId") {
      if (value === "required") return ["required"];
      return Array.isArray(value) ? value.map(String) : [];
    }
    if (Array.isArray(value)) return value.map((item) => String(item));
    if (typeof value === "string" && value) return [value];
    return [];
  }

  function conditionFieldKeys(rule: RoutingRule): string[] {
    return Object.keys(rule.when || {});
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
        cleanupOldPosts: channel.cleanupOldPosts !== false,
        isDefault: Boolean(channel.isDefault),
        allowedUserIds: parseIds(channel.collaboratorText),
      });
    }
    const knownIds = new Set(result.map((channel) => channel.id));
    const selectedRoute = (value: unknown) => {
      const id = String(value || "");
      return knownIds.has(id) ? id : "";
    };
    const rules: RoutingRule[] = [];
    for (const rule of state.routing.rules || []) {
      if (!rule.channelId) {
        state.validationMessage = "每条路由规则都要选择投递到的频道（或删掉这条规则）。";
        return null;
      }
      if (!knownIds.has(rule.channelId)) {
        state.validationMessage = "路由规则里有频道被删除，请重新选择投递频道。";
        return null;
      }
      rules.push({
        id: rule.id,
        name: String(rule.name || "").trim().slice(0, 60),
        enabled: rule.enabled !== false,
        when: rule.when || {},
        channelId: rule.channelId,
      });
    }
    if (rules.length > 40) {
      state.validationMessage = "路由规则最多 40 条。";
      return null;
    }
    const routing: Routing = {
      version: 2,
      rules,
      fallbackChannelId: selectedRoute(state.routing.fallbackChannelId),
      publicReleaseGroups: parseLines(state.releaseGroupsText),
      ...legacyShadowKeys({ rules, fallbackChannelId: selectedRoute(state.routing.fallbackChannelId), publicReleaseGroups: parseLines(state.releaseGroupsText) }),
    };
    return { channels: result, routing };
  }

  // 用 reactive 包装返回值：跨组件透传时模板里 channelOptions/isDirty 等 computed 自动解包。
  return reactive({
    state,
    channelOptions,
    enabledChannelOptions,
    enabledChannelCount,
    isDirty,
    applyConfig,
    resetToEmpty,
    addChannel,
    setDefault,
    removeChannel,
    buildPayload,
    markClean,
    addRule,
    removeRule,
    moveRule,
    setConditionValues,
    conditionValues,
    conditionFieldKeys,
  });
}

export type ChannelConfigEditor = ReturnType<typeof useChannelConfigEditor>;
