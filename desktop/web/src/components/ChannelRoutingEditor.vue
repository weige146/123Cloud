// 频道卡片网格 + v2 路由规则编辑器 + 规则模拟器：模板与样式源自 ChannelSettingsView，
// 编辑状态由 useChannelConfigEditor 提供并通过 editor prop 注入。
// 规则语义（有序、首条命中即停、条件间 AND/值内 OR）与后端 app/submission_routing.py 一致，
// 字段与取值词表由 /api/submission/routing/schema 下发，不在此硬编码。
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type { ChannelConfigEditor } from "@/composables/useChannelConfigEditor";
import type { RoutingRule, RoutingSchemaField } from "@/api/types";
import { channelOwnerApi } from "@/api";

const props = defineProps<{ editor: ChannelConfigEditor; ownerUserId?: number }>();

const roleItems = [
  { title: "私有频道", value: "private" },
  { title: "公开频道（完结内容）", value: "public_completed" },
  { title: "公开频道（连载内容）", value: "public_updating" },
];

const schemaFields = ref<RoutingSchemaField[]>([
  // 接口拉取失败时的兜底词表（与后端 routing_schema() 保持一致）
  { key: "linkType", label: "链接类型", kind: "multi", values: [{ value: "share", label: "分享链接" }, { value: "fastlink", label: "秒传链接" }] },
  { key: "mediaType", label: "媒体类型", kind: "multi", values: [{ value: "movie", label: "电影" }, { value: "tv", label: "剧集" }, { value: "unknown", label: "未识别" }] },
  { key: "completion", label: "完结状态", kind: "multi", values: [{ value: "completed", label: "完结" }, { value: "updating", label: "连载/未完结" }, { value: "unknown", label: "完结状态未知" }] },
  { key: "releaseGroupState", label: "发布组状态", kind: "multi", values: [{ value: "detected", label: "识别到发布组" }, { value: "stripped", label: "导出时已去除" }, { value: "none", label: "没有发布组" }] },
  { key: "releaseGroupWhitelist", label: "发布组与白名单", kind: "single", values: [{ value: "notIn", label: "不在白名单（走私有档）" }, { value: "in", label: "全部在白名单" }] },
  { key: "releaseGroups", label: "发布组（自定义名单）", kind: "freeMulti", values: [] },
  { key: "quality", label: "画质档位", kind: "multi", values: [{ value: "8k", label: "8K" }, { value: "4k", label: "4K/2160p" }, { value: "1080p", label: "1080p" }, { value: "720p", label: "720p" }, { value: "sd", label: "SD/480p 及以下" }] },
  { key: "source", label: "资源来源", kind: "freeMulti", values: ["UHD BluRay Remux", "BluRay Remux", "WEB-DL", "WEBRip", "BluRay", "Remux", "SD"].map((value) => ({ value, label: value })) },
  { key: "submitter", label: "投稿人", kind: "multi", values: [{ value: "self", label: "频道主本人" }, { value: "collaborator", label: "协作者投稿" }] },
  { key: "tmdbId", label: "TMDB ID", kind: "idList", values: [] },
]);

const fieldByKey = computed(() => new Map(schemaFields.value.map((field) => [field.key, field])));
const conditionSelectItems = computed(() => schemaFields.value.map((field) => ({ title: field.label, value: field.key })));

function fieldLabel(key: string): string {
  return fieldByKey.value.get(key)?.label || key;
}

function fieldKind(key: string): RoutingSchemaField["kind"] {
  return fieldByKey.value.get(key)?.kind || "multi";
}

function fieldValueOptions(key: string) {
  const field = fieldByKey.value.get(key);
  if (!field) return [];
  if (field.kind === "idList") {
    return [{ title: "已匹配到 TMDB（不限 ID）", value: "required" }];
  }
  return field.values.map((item) => ({ title: item.label, value: item.value }));
}

function addableFields(rule: RoutingRule): { title: string; value: string }[] {
  const used = new Set(Object.keys(rule.when || {}));
  return conditionSelectItems.value.filter((item) => !used.has(item.value));
}

function conditionModel(rule: RoutingRule, key: string): string[] {
  return props.editor.conditionValues(rule, key);
}

function onConditionUpdate(rule: RoutingRule, key: string, raw: unknown) {
  const values = (Array.isArray(raw) ? raw : raw === null || raw === undefined || raw === "" ? [] : [raw]).map((item) => String(item));
  props.editor.setConditionValues(rule, key, values);
}

function onSingleUpdate(rule: RoutingRule, raw: unknown) {
  const value = typeof raw === "object" && raw !== null && "value" in raw ? String((raw as { value: unknown }).value) : raw ? String(raw) : "";
  props.editor.setConditionValues(rule, "releaseGroupWhitelist", value ? [value] : []);
}

function removeCondition(rule: RoutingRule, key: string) {
  props.editor.setConditionValues(rule, key, []);
}

function addCondition(rule: RoutingRule, key: string) {
  if (!key) return;
  if (key === "releaseGroupWhitelist") {
    props.editor.setConditionValues(rule, key, ["notIn"]);
  } else if (key === "tmdbId") {
    props.editor.setConditionValues(rule, key, ["required"]);
  } else {
    // 空数组会被规范化成「删键」，给一个默认值让条件行立刻可见可编辑
    const options = fieldValueOptions(key);
    props.editor.setConditionValues(rule, key, options.length ? [String(options[0].value)] : [""]);
  }
}

// ===== 规则模拟器 =====
const simulateOpen = ref(false);
const simulateText = ref("");
const simulateBusy = ref(false);
const simulateError = ref("");
const simulateResults = ref<Awaited<ReturnType<typeof channelOwnerApi.simulate>>["results"]>([]);

const factLabels: Record<string, string> = {
  linkType: "链接类型",
  mediaType: "媒体类型",
  completion: "完结状态",
  releaseGroupState: "发布组状态",
  releaseGroups: "发布组",
  quality: "画质",
  source: "来源",
  submitter: "投稿人",
  tmdbId: "TMDB ID",
};

async function runSimulate() {
  if (!props.ownerUserId) {
    simulateError.value = "请先选择账号";
    return;
  }
  if (!simulateText.value.trim()) {
    simulateError.value = "请先粘贴一段分享链接或秒传链接";
    return;
  }
  simulateBusy.value = true;
  simulateError.value = "";
  simulateResults.value = [];
  try {
    const data = await channelOwnerApi.simulate(props.ownerUserId, simulateText.value);
    simulateResults.value = data.results || [];
  } catch (cause) {
    simulateError.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    simulateBusy.value = false;
  }
}

function onAddCondition(rule: RoutingRule, key: unknown) {
  addCondition(rule, key ? String(key) : "");
}

function factRecord(facts: object | undefined): Record<string, unknown> | undefined {
  return facts as Record<string, unknown> | undefined;
}

function factText(facts: Record<string, unknown> | undefined, key: string): string {
  if (!facts) return "—";
  const value = facts[key];
  if (Array.isArray(value)) return value.length ? value.join("、") : "无";
  if (key === "tmdbId") return Number(value) > 0 ? String(value) : "未匹配";
  return String(value ?? "—") || "—";
}

onMounted(async () => {
  try {
    const data = await channelOwnerApi.routingSchema();
    if (data.schema?.fields?.length) schemaFields.value = data.schema.fields;
  } catch {
    /* 离线/旧后端：用内置兜底词表 */
  }
});
</script>

<template>
  <div class="routing-editor">
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
        <v-btn color="primary" prepend-icon="mdi-plus" @click="editor.addChannel()">添加频道</v-btn>
      </header>

      <div class="channel-grid">
        <article v-for="(channel, index) in editor.state.channels" :key="channel.id" class="channel-card" :class="{ 'channel-card--default': channel.isDefault }">
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
                @update:model-value="editor.setDefault(channel, Boolean($event))"
              />
              <v-btn size="small" color="error" variant="text" icon="mdi-delete-outline" aria-label="删除频道" @click="editor.removeChannel(channel)" />
            </div>
          </div>
          <div class="field-grid">
            <v-text-field v-model="channel.title" label="显示名称" placeholder="例如：我的私有频道" variant="outlined" density="comfortable" hide-details />
            <v-text-field v-model="channel.chatId" label="频道 Chat ID" placeholder="-100xxxxxxxxxx" variant="outlined" density="comfortable" hide-details />
            <v-select v-model="channel.role" label="频道类型" :items="roleItems" variant="outlined" density="comfortable" hide-details />
            <div class="channel-switches">
              <v-switch v-model="channel.enabled" label="启用这个频道" color="primary" density="compact" hide-details />
              <v-switch v-model="channel.cleanupOldPosts" label="发布后清理旧帖" color="primary" density="compact" hide-details />
            </div>
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

    <!-- 自动路由：v2 有序规则列表，语义与后端 submission_routing.decide_submission_channel 一致 -->
    <section class="glass-section">
      <header class="section-head">
        <div class="section-head-left">
          <div class="section-icon section-icon--info"><v-icon size="18">mdi-routes-outline</v-icon></div>
          <div>
            <h2>自动路由规则</h2>
            <p>规则从上到下逐条匹配，<strong>第一条全部条件命中的规则决定投递频道</strong>；一条都没命中走「兜底频道」。手动指定的频道永远优先于规则。</p>
          </div>
        </div>
        <div class="section-head-actions">
          <v-btn v-if="ownerUserId" variant="tonal" prepend-icon="mdi-test-tube" @click="simulateOpen = !simulateOpen">模拟器</v-btn>
          <v-btn color="primary" prepend-icon="mdi-plus" :disabled="(editor.state.routing.rules || []).length >= 40" @click="editor.addRule()">添加规则</v-btn>
        </div>
      </header>

      <v-alert v-if="!(editor.state.routing.rules || []).length" type="info" variant="tonal" density="compact" class="rules-empty">
        还没有规则：投稿会直接走「兜底频道」；都没配置时按频道类型（私有/完结/连载）自动选择。常用起手：先加一条「链接类型=秒传链接」投递到秒传频道，再加「完结内容」「连载内容」各一条。
      </v-alert>

      <article v-for="(rule, index) in editor.state.routing.rules || []" :key="rule.id" class="rule-card" :class="{ 'rule-card--off': rule.enabled === false }">
        <div class="rule-card-head">
          <span class="channel-card-index">{{ index + 1 }}</span>
          <v-switch :model-value="rule.enabled !== false" color="primary" density="compact" hide-details aria-label="启用这条规则" @update:model-value="rule.enabled = Boolean($event)" />
          <v-text-field v-model="rule.name" label="规则名称（可选）" placeholder="例如：秒传帖进秒传频道" variant="outlined" density="comfortable" hide-details class="rule-name" />
          <div class="rule-order">
            <v-btn icon="mdi-arrow-up" variant="text" size="small" :disabled="index === 0" aria-label="上移" @click="editor.moveRule(index, -1)" />
            <v-btn icon="mdi-arrow-down" variant="text" size="small" :disabled="index === (editor.state.routing.rules || []).length - 1" aria-label="下移" @click="editor.moveRule(index, 1)" />
            <v-btn icon="mdi-delete-outline" color="error" variant="text" size="small" aria-label="删除规则" @click="editor.removeRule(rule.id)" />
          </div>
        </div>
        <div class="rule-body">
          <div v-if="!Object.keys(rule.when || {}).length" class="rule-always">
            <v-icon size="14">mdi-all-inclusive</v-icon>
            无条件命中（任何投稿都走这条）——通常放最后一条当默认档
          </div>
          <div v-for="key in Object.keys(rule.when || {})" :key="key" class="condition-row">
            <div class="condition-label">{{ fieldLabel(key) }}</div>
            <v-select
              v-if="fieldKind(key) === 'single'"
              :model-value="conditionModel(rule, key)[0] || ''"
              :items="fieldValueOptions(key)"
              item-title="title"
              item-value="value"
              variant="outlined"
              density="comfortable"
              hide-details
              class="condition-control"
              @update:model-value="onSingleUpdate(rule, $event)"
            />
            <v-combobox
              v-else-if="fieldKind(key) === 'freeMulti' || fieldKind(key) === 'idList'"
              :model-value="conditionModel(rule, key)"
              :items="fieldValueOptions(key)"
              item-title="title"
              item-value="value"
              multiple
              chips
              small-chips
              closable-chips
              menu-icon=""
              :placeholder="fieldKind(key) === 'idList' ? '输入 TMDB ID 后回车' : '输入后回车，可加多个'"
              variant="outlined"
              density="comfortable"
              hide-details
              class="condition-control"
              @update:model-value="onConditionUpdate(rule, key, $event)"
            />
            <v-select
              v-else
              :model-value="conditionModel(rule, key)"
              :items="fieldValueOptions(key)"
              item-title="title"
              item-value="value"
              multiple
              chips
              small-chips
              :closable-chips="false"
              variant="outlined"
              density="comfortable"
              hide-details
              class="condition-control"
              @update:model-value="onConditionUpdate(rule, key, $event)"
            />
            <v-btn icon="mdi-close" variant="text" size="small" aria-label="移除这个条件" @click="removeCondition(rule, key)" />
          </div>
          <div class="rule-target">
            <div class="condition-label">投递到</div>
            <v-select v-model="rule.channelId" :items="editor.enabledChannelOptions" item-title="title" item-value="value" placeholder="选择频道" variant="outlined" density="comfortable" hide-details class="condition-control" />
          </div>
          <v-select
            v-if="addableFields(rule).length"
            :model-value="null"
            :items="addableFields(rule)"
            item-title="title"
            item-value="value"
            placeholder="+ 添加条件"
            prepend-inner-icon="mdi-filter-variant"
            variant="outlined"
            density="compact"
            hide-details
            class="rule-add-condition"
            @update:model-value="onAddCondition(rule, $event)"
          />
        </div>
      </article>

      <div class="route-footer-grid">
        <v-select
          v-model="editor.state.routing.fallbackChannelId"
          :items="editor.channelOptions"
          item-title="title"
          item-value="value"
          label="兜底频道（所有规则都没命中时）"
          variant="outlined"
          density="comfortable"
          hide-details
        />
      </div>
      <v-textarea
        v-model="editor.state.releaseGroupsText"
        label="发布组白名单"
        placeholder="每行一个发布组名称；给「发布组与白名单」条件用的名单——命中白名单的不算私有档内容"
        :rows="3"
        variant="outlined"
        density="comfortable"
        hide-details
        class="collaborator-field"
      />
    </section>

    <!-- 规则模拟器：按当前（未保存亦可）配置试跑链接；保存后结果与真实投稿一致 -->
    <section v-if="simulateOpen" class="glass-section">
      <header class="section-head">
        <div class="section-head-left">
          <div class="section-icon section-icon--info"><v-icon size="18">mdi-flask-outline</v-icon></div>
          <div>
            <h2>路由模拟器</h2>
            <p>粘贴一段分享链接或秒传链接试跑路由（秒传链接本地解析；分享链接会实际访问 123 接口拉文件名清单）。模拟不含发布组/画质等按文件名识别的字段时结果偏保守。</p>
          </div>
        </div>
        <v-btn color="primary" variant="tonal" :loading="simulateBusy" prepend-icon="mdi-play" @click="runSimulate">试跑</v-btn>
      </header>
      <v-textarea v-model="simulateText" :rows="3" placeholder="https://www.123pan.com/s/xxxx 或 123FLCPV2$…" variant="outlined" density="comfortable" hide-details />
      <v-alert v-if="simulateError" type="error" variant="tonal" density="compact" class="simulate-alert" closable @click:close="simulateError = ''">{{ simulateError }}</v-alert>
      <div v-for="(item, index) in simulateResults" :key="index" class="simulate-item">
        <div class="simulate-item-head">
          <v-icon size="16" :color="item.error ? 'error' : 'success'">{{ item.error ? "mdi-alert-circle-outline" : "mdi-check-circle-outline" }}</v-icon>
          <strong>{{ item.title || item.url }}</strong>
          <v-chip size="x-small" variant="tonal">{{ item.provider === "123fastlink" ? "秒传" : "分享" }}</v-chip>
          <span v-if="item.channel" class="simulate-channel">
            → {{ item.channel.title }}
            <em v-if="item.decision?.ruleName">（{{ item.decision.mode }} · 命中「{{ item.decision.ruleName }}」）</em>
            <em v-else>（{{ item.decision?.summary || "" }}）</em>
          </span>
          <span v-else-if="!item.error" class="simulate-channel">→ 未匹配到频道</span>
        </div>
        <div v-if="item.error" class="simulate-error">{{ item.error }}</div>
        <div v-else-if="item.facts" class="simulate-facts">
          <span v-for="key in ['linkType', 'mediaType', 'completion', 'releaseGroupState', 'quality', 'source', 'submitter', 'tmdbId']" :key="key">
            {{ factLabels[key] }}: {{ factText(factRecord(item.facts), key) }}
          </span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
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

.section-head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
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

.channel-switches {
  display: flex;
  align-items: center;
  gap: 18px;
  padding-left: 2px;
}

.collaborator-field {
  margin-top: 12px;
}

/* 规则卡片 */
.rules-empty {
  margin-bottom: 12px;
}

.rule-card {
  padding: 14px 16px;
  margin-bottom: 12px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}

.rule-card--off {
  opacity: 0.55;
}

.rule-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.rule-name {
  flex: 1;
  min-width: 0;
}

.rule-order {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.rule-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 34px;
}

.rule-always {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}

.condition-row,
.rule-target {
  display: flex;
  align-items: center;
  gap: 10px;
}

.condition-label {
  width: 120px;
  flex-shrink: 0;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.condition-control {
  flex: 1;
  min-width: 0;
}

.rule-add-condition {
  max-width: 240px;
}

.route-footer-grid {
  display: grid;
  grid-template-columns: minmax(280px, 420px);
  gap: 12px;
  margin-top: 6px;
}

/* 模拟器 */
.simulate-alert {
  margin-top: 10px;
}

.simulate-item {
  margin-top: 12px;
  padding: 12px 14px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}

.simulate-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--text-primary);
}

.simulate-channel {
  color: var(--accent);
  font-weight: 600;
}

.simulate-channel em {
  font-style: normal;
  font-weight: 400;
  color: var(--text-muted);
}

.simulate-error {
  margin-top: 6px;
  font-size: 12px;
  color: var(--error, #ef4444);
}

.simulate-facts {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 11.5px;
  color: var(--text-muted);
}

@media (max-width: 720px) {
  .field-grid,
  .route-footer-grid {
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

  .rule-body {
    padding-left: 0;
  }
}
</style>
