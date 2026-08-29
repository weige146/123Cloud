<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { submissionApi } from "@/api";
import { useGlobalState } from "@/composables/useGlobalState";
import { slugId } from "@/utils/format";
import type {
  SourceLabel,
  SubmissionConfig,
  SubmissionDisplayPreview,
  SubmissionDisplayPreviewSample,
} from "@/api/types";
import PageHero from "@/components/PageHero.vue";
import GlassCard from "@/components/GlassCard.vue";
import FormGrid from "@/components/FormGrid.vue";
import SegmentedTabs from "@/components/SegmentedTabs.vue";
import { useResponsive } from "@/composables/useResponsive";

const { state, writeSubmissionConfig, notifySuccess, notifyError } = useGlobalState();
const { isMobile } = useResponsive();

const defaultCaption = [
  "🎬 <b>{title}</b>",
  "",
  "{tmdbMarker}",
  "⭐ TMDB评分：{tmdbRating}",
  "🍿 豆瓣评分：{doubanRating}",
  "🖥️ 画质：{quality}",
  "💽 视频：{source}",
  "💾 大小：{size}",
  "📣 路由：{routeChannel}",
  "👤 分享：{shareLink}",
  "{resourceBlock}",
  "{overviewBlock}",
  "",
  "🏷 标签：{tags}",
].join("\n");

const defaultSourceLabels: SourceLabel[] = [
  { id: "display_uhd_bluray_remux", name: "UHD BluRay Remux", enabled: true, source: "UHD BluRay Remux", template: "{{resolution4k}}蓝光原盘REMUX", order: 100 },
  { id: "display_bluray_remux", name: "BluRay Remux", enabled: true, source: "BluRay Remux", template: "{{resolution}}蓝光原盘REMUX", order: 90 },
  { id: "display_uhd_bluray", name: "UHD BluRay", enabled: true, source: "UHD BluRay", template: "{{resolution4k}}蓝光原盘压制", order: 80 },
  { id: "display_bluray", name: "BluRay", enabled: true, source: "BluRay", template: "{{resolution}}蓝光原盘压制", order: 70 },
  { id: "display_uhdtv", name: "UHDTV", enabled: true, source: "UHDTV", template: "{{resolution4k}} UHDTV", order: 60 },
];

const captionTokens = [
  "{title}",
  "{tmdbMarker}",
  "{tmdbRating}",
  "{doubanRating}",
  "{quality}",
  "{source}",
  "{size}",
  "{routeChannel}",
  "{shareLink}",
  "{resourceBlock}",
  "{overviewBlock}",
  "{tags}",
];

const sourceTokens = ["{{resolution4k}}", "{{resolution}}", "{{source}}"];

const shareName = ref("123");
const shareUrl = ref("");
const caption = ref(defaultCaption);
const sourceLabels = ref<SourceLabel[]>([]);
const saving = ref(false);
const previewLoading = ref(false);
const previewError = ref("");
const preview = ref<SubmissionDisplayPreview | null>(null);
const activeSourceIndex = ref(0);
const captionField = ref<any>(null);
const mobilePane = ref<"edit" | "preview">("edit");
const mobilePaneTabs = [
  { key: "edit", label: "编辑", icon: "mdi-pencil-ruler" },
  { key: "preview", label: "预览", icon: "mdi-eye-outline" },
];

const sample = reactive<SubmissionDisplayPreviewSample>({
  title: "仙逆",
  year: "2023",
  mediaType: "tv",
  quality: "2160p",
  source: "UHD BluRay Remux",
  webSource: "",
  effect: "HDR10",
  fps: "60fps",
  videoCodec: "HEVC",
  audioCodec: "DDP2.0",
  size: "192.10 GB",
  releaseGroup: "HiveWeb",
  seasonEpisode: "S01E01",
  overview: "这里显示 TMDB 简介或手动备注。",
});

const sampleFileNames = ref("Renegade.Immortal.S01E01.2023.2160p.UHD.BluRay.REMUX.HDR10.60fps.HEVC.DDP2.0-HiveWeb.mkv");

let previewTimer: number | undefined;
let previewRequestId = 0;

const sourceOptions = computed(() => {
  const defaults = ["UHD BluRay Remux", "BluRay Remux", "UHD BluRay", "BluRay", "UHDTV", "WEB-DL", "WEBRip", "HDTV"];
  return Array.from(new Set([...sourceLabels.value.map((item) => item.source).filter(Boolean), ...defaults]));
});

const enabledSourceLabelCount = computed(() => sourceLabels.value.filter((item) => item.enabled !== false && item.source && item.template).length);
const previewText = computed(() => stripTelegramHtml(preview.value?.caption || ""));

function cloneConfig(config: unknown): SubmissionConfig {
  return JSON.parse(JSON.stringify(config || {}));
}

function sortedLabels(labels: SourceLabel[]): SourceLabel[] {
  return [...labels].sort((a, b) => Number(b.order || 0) - Number(a.order || 0));
}

function syncFromConfig() {
  const config = state.submissionConfig;
  if (!config) return;
  const templates = config.templates || {};
  shareName.value = String(templates.shareName ?? "") || "123";
  shareUrl.value = String(templates.shareUrl ?? "");
  caption.value = String(templates.caption ?? "") || defaultCaption;
  sourceLabels.value = sortedLabels((((config.ruleConfig || {}).display || {}).sourceLabels || []).map((item, index) => ({
    id: item.id || `display_${index}_${slugId(String(item.source ?? "") || "source")}`,
    name: String(item.name ?? item.source ?? ""),
    enabled: item.enabled !== false,
    source: String(item.source ?? ""),
    template: item.template || "",
    order: item.order ?? 100 - index * 10,
  })));
  if (!sourceLabels.value.length) {
    sourceLabels.value = defaultSourceLabels.map((item) => ({ ...item }));
  }
  schedulePreview();
}

watch(
  () => state.loaded,
  (loaded) => {
    if (loaded) syncFromConfig();
  },
  { immediate: true }
);

watch(
  () => state.submissionConfig,
  () => {
    if (state.loaded) syncFromConfig();
  }
);

watch(
  () => [
    shareName.value,
    shareUrl.value,
    caption.value,
    JSON.stringify(sourceLabels.value),
    JSON.stringify(sample),
    sampleFileNames.value,
  ],
  () => schedulePreview(),
  { deep: true }
);

function normalizeSourceLabels(): SourceLabel[] {
  return sourceLabels.value
    .filter((item) => String(item.source ?? "").trim() && String(item.template ?? "").trim())
    .map((item, index) => ({
      ...item,
      id: item.id || `display_${index}_${slugId(String(item.source ?? ""))}`,
      name: String(item.name ?? item.source ?? ""),
      enabled: item.enabled !== false,
      source: String(item.source ?? "").trim(),
      template: String(item.template ?? "").trim(),
      order: 100 - index * 10,
    }));
}

function buildEditedConfig(): SubmissionConfig {
  const next = cloneConfig(state.submissionConfig);
  next.templates = {
    ...(next.templates || {}),
    shareName: shareName.value.trim() || "123",
    shareUrl: shareUrl.value.trim(),
    caption: caption.value,
  };
  next.ruleConfig = next.ruleConfig || {};
  next.ruleConfig.display = next.ruleConfig.display || {};
  next.ruleConfig.display.sourceLabels = normalizeSourceLabels();
  return next;
}

function buildSample(): SubmissionDisplayPreviewSample {
  return {
    ...sample,
    fileNames: sampleFileNames.value
      .split(/\n+/)
      .map((item) => item.trim())
      .filter(Boolean),
    shareUrl: shareUrl.value.trim(),
  };
}

function addSourceLabel() {
  sourceLabels.value.push({
    id: `display_${Date.now()}`,
    name: "",
    enabled: true,
    source: "",
    template: "",
    order: 100 - sourceLabels.value.length * 10,
  });
  activeSourceIndex.value = sourceLabels.value.length - 1;
}

function removeSourceLabel(index: number) {
  sourceLabels.value.splice(index, 1);
  activeSourceIndex.value = Math.max(0, Math.min(activeSourceIndex.value, sourceLabels.value.length - 1));
}

function duplicateSourceLabel(index: number) {
  const item = sourceLabels.value[index];
  if (!item) return;
  sourceLabels.value.splice(index + 1, 0, {
    ...item,
    id: `display_${Date.now()}`,
    source: `${item.source} Copy`,
    name: `${item.name || item.source} Copy`,
  });
  activeSourceIndex.value = index + 1;
}

function moveSourceLabel(index: number, offset: number) {
  const nextIndex = index + offset;
  if (nextIndex < 0 || nextIndex >= sourceLabels.value.length) return;
  const next = [...sourceLabels.value];
  const [item] = next.splice(index, 1);
  next.splice(nextIndex, 0, item);
  sourceLabels.value = next;
  activeSourceIndex.value = nextIndex;
}

function updateLabel<K extends keyof SourceLabel>(index: number, key: K, value: SourceLabel[K]) {
  sourceLabels.value[index] = { ...sourceLabels.value[index], [key]: value };
}

async function insertCaptionToken(token: string) {
  const textarea = captionField.value?.$el?.querySelector("textarea") as HTMLTextAreaElement | null;
  if (!textarea) {
    caption.value = `${caption.value}${token}`;
    return;
  }
  const start = textarea.selectionStart ?? caption.value.length;
  const end = textarea.selectionEnd ?? caption.value.length;
  caption.value = `${caption.value.slice(0, start)}${token}${caption.value.slice(end)}`;
  await nextTick();
  textarea.focus();
  textarea.setSelectionRange(start + token.length, start + token.length);
}

function appendSourceToken(token: string) {
  const index = Math.min(Math.max(activeSourceIndex.value, 0), Math.max(sourceLabels.value.length - 1, 0));
  if (!sourceLabels.value[index]) addSourceLabel();
  const item = sourceLabels.value[index];
  sourceLabels.value[index] = {
    ...item,
    template: `${item.template || ""}${token}`,
  };
}

async function save() {
  saving.value = true;
  try {
    await writeSubmissionConfig(buildEditedConfig());
    notifySuccess("投稿展示已保存");
  } catch (error) {
    notifyError(`投稿展示保存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    saving.value = false;
  }
}

function schedulePreview(immediate = false) {
  if (!state.loaded) return;
  if (previewTimer) window.clearTimeout(previewTimer);
  if (immediate) {
    void refreshPreview();
    return;
  }
  previewTimer = window.setTimeout(() => {
    void refreshPreview();
  }, 260);
}

async function refreshPreview() {
  const requestId = ++previewRequestId;
  previewLoading.value = true;
  previewError.value = "";
  try {
    const data = await submissionApi.previewDisplay(buildEditedConfig(), buildSample());
    if (requestId === previewRequestId) {
      preview.value = data.preview;
    }
  } catch (error) {
    if (requestId === previewRequestId) {
      previewError.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    if (requestId === previewRequestId) {
      previewLoading.value = false;
    }
  }
}

function stripTelegramHtml(value: string): string {
  const withBreaks = String(value || "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/blockquote>/gi, "\n")
    .replace(/<blockquote[^>]*>/gi, "")
    .replace(/<\/p>/gi, "\n")
    .replace(/<[^>]+>/g, "");
  const element = document.createElement("textarea");
  element.innerHTML = withBreaks;
  return element.value.replace(/\n{3,}/g, "\n\n").trim();
}

onMounted(() => {
  if (state.loaded) syncFromConfig();
});
</script>

<template>
  <div class="page fade-rise display-view" data-group="dashboard">
    <PageHero
      group="dashboard"
      icon="mdi-file-document-outline"
      title="投稿展示"
      desc="分享按钮、Caption 模板和片源备注映射。"
    >
      <template #actions>
        <v-btn variant="text" :loading="previewLoading" @click="refreshPreview">
          <v-icon start>mdi-refresh</v-icon>
          刷新预览
        </v-btn>
        <v-btn color="primary" :loading="saving" @click="save">
          <v-icon start>mdi-content-save</v-icon>
          保存
        </v-btn>
      </template>
    </PageHero>

    <SegmentedTabs v-if="isMobile" v-model="mobilePane" :tabs="mobilePaneTabs" full-width />

    <div class="display-grid">
      <GlassCard
        v-show="!isMobile || mobilePane === 'edit'"
        accent="group"
        icon="mdi-pencil-ruler"
        title="模板编辑器"
        desc="分享按钮、Caption 模板和片源备注映射。"
      >
        <FormGrid>
          <v-text-field
            v-model="shareName"
            label="分享名称"
            placeholder="123"
            variant="outlined"
            density="compact"
            hide-details
          />
          <v-text-field
            v-model="shareUrl"
            label="分享主页"
            placeholder="https://t.me/your_channel"
            variant="outlined"
            density="compact"
            hide-details
          />
        </FormGrid>

        <section class="editor-section">
          <div class="section-head">
            <div>
              <h3 class="section-title">Caption 模板</h3>
              <p class="section-meta">{{ caption.length }} 字符</p>
            </div>
            <div class="token-row">
              <v-chip
                v-for="token in captionTokens"
                :key="token"
                size="small"
                variant="tonal"
                @click="insertCaptionToken(token)"
              >
                {{ token }}
              </v-chip>
            </div>
          </div>

          <v-textarea
            ref="captionField"
            v-model="caption"
            :rows="11"
            auto-grow
            variant="outlined"
            density="compact"
            hide-details
            class="caption-editor"
          />
        </section>

        <section class="editor-section">
          <div class="section-head">
            <div>
              <h3 class="section-title">片源备注模板</h3>
              <p class="section-meta">{{ enabledSourceLabelCount }} 条启用</p>
            </div>
            <div class="source-tools">
              <v-btn-toggle divided density="compact" variant="outlined">
                <v-btn
                  v-for="token in sourceTokens"
                  :key="token"
                  size="small"
                  @click="appendSourceToken(token)"
                >
                  {{ token }}
                </v-btn>
              </v-btn-toggle>
              <v-btn variant="outlined" prepend-icon="mdi-plus" @click="addSourceLabel">新增</v-btn>
            </div>
          </div>

          <div class="source-list">
            <div
              v-for="(label, index) in sourceLabels"
              :key="label.id || index"
              class="source-row"
              :class="{ active: activeSourceIndex === index, disabled: label.enabled === false }"
              @focusin="activeSourceIndex = index"
            >
              <v-switch
                :model-value="label.enabled !== false"
                color="primary"
                density="compact"
                hide-details
                inset
                @update:model-value="(value: boolean | null) => updateLabel(index, 'enabled', Boolean(value))"
              />
              <v-text-field
                :model-value="label.source"
                label="片源"
                placeholder="UHD BluRay"
                variant="outlined"
                density="compact"
                hide-details
                @focus="activeSourceIndex = index"
                @update:model-value="(value: string) => updateLabel(index, 'source', value)"
              />
              <v-text-field
                :model-value="label.template"
                label="模板"
                placeholder="{{resolution4k}}蓝光原盘压制"
                variant="outlined"
                density="compact"
                hide-details
                @focus="activeSourceIndex = index"
                @update:model-value="(value: string) => updateLabel(index, 'template', value)"
              />
              <div class="row-actions">
                <v-btn icon variant="text" size="small" title="上移" :disabled="index === 0" @click="moveSourceLabel(index, -1)">
                  <v-icon size="18">mdi-arrow-up</v-icon>
                </v-btn>
                <v-btn icon variant="text" size="small" title="下移" :disabled="index === sourceLabels.length - 1" @click="moveSourceLabel(index, 1)">
                  <v-icon size="18">mdi-arrow-down</v-icon>
                </v-btn>
                <v-btn icon variant="text" size="small" title="复制" @click="duplicateSourceLabel(index)">
                  <v-icon size="18">mdi-content-copy</v-icon>
                </v-btn>
                <v-btn icon variant="text" color="error" size="small" title="删除" @click="removeSourceLabel(index)">
                  <v-icon size="18">mdi-delete-outline</v-icon>
                </v-btn>
              </div>
            </div>
          </div>
        </section>
      </GlassCard>

      <GlassCard
        v-show="!isMobile || mobilePane === 'preview'"
        accent="info"
        icon="mdi-eye-outline"
        title="实时预览"
        desc="后端按当前表单生成。"
      >
        <template #actions>
          <v-btn icon variant="text" :loading="previewLoading" title="刷新预览" @click="refreshPreview">
            <v-icon size="20">mdi-refresh</v-icon>
          </v-btn>
        </template>

        <div class="preview-form">
          <v-text-field v-model="sample.title" label="标题" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="sample.year" label="年份" variant="outlined" density="compact" hide-details />
          <v-select
            v-model="sample.mediaType"
            :items="[
              { title: '剧集', value: 'tv' },
              { title: '电影', value: 'movie' },
            ]"
            label="类型"
            variant="outlined"
            density="compact"
            hide-details
          />
          <v-text-field v-model="sample.seasonEpisode" label="季集" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="sample.quality" label="画质" variant="outlined" density="compact" hide-details />
          <v-combobox v-model="sample.source" :items="sourceOptions" label="片源" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="sample.videoCodec" label="视频编码" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="sample.audioCodec" label="音频编码" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="sample.effect" label="HDR/特效" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="sample.releaseGroup" label="发布组" variant="outlined" density="compact" hide-details />
          <v-textarea v-model="sampleFileNames" label="文件名" :rows="3" variant="outlined" density="compact" hide-details class="span-full" />
          <v-textarea v-model="sample.overview" label="简介" :rows="3" variant="outlined" density="compact" hide-details class="span-full" />
        </div>

        <div class="preview-meta">
          <div class="preview-meta-tile">
            <span class="preview-meta-label">资源名</span>
            <strong class="preview-meta-value mono-value">{{ preview?.resourceName || "等待预览" }}</strong>
          </div>
          <div class="preview-meta-tile">
            <span class="preview-meta-label">路由</span>
            <strong class="preview-meta-value mono-value">{{ preview?.routeChannel || "等待预览" }}</strong>
          </div>
        </div>

        <div v-if="previewError" class="preview-error">
          <v-icon size="14">mdi-alert-circle</v-icon>
          {{ previewError }}
        </div>
        <div class="preview-output" :class="{ loading: previewLoading }">
          <pre>{{ previewText || "等待预览" }}</pre>
        </div>
      </GlassCard>
    </div>
  </div>
</template>

<style scoped>
.display-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(340px, 0.85fr);
  gap: 16px;
  align-items: start;
}

.editor-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 16px;
}

.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
}

.section-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.section-meta {
  margin: 2px 0 0;
  color: var(--text-muted);
  font-size: 11px;
}

.token-row,
.source-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}

.caption-editor :deep(textarea) {
  font-family: var(--font-mono);
  line-height: 1.5;
  font-size: 12px;
}

.source-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.source-row {
  display: grid;
  grid-template-columns: 56px minmax(140px, 0.8fr) minmax(200px, 1.2fr) auto;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--glass-border-3);
  border-radius: var(--radius-md);
  background: var(--glass-bg-3);
  backdrop-filter: blur(var(--glass-blur-3)) saturate(var(--glass-saturate));
  -webkit-backdrop-filter: blur(var(--glass-blur-3)) saturate(var(--glass-saturate));
  transition: background-color var(--transition), border-color var(--transition);
}

.source-row.active {
  border-color: var(--group-color);
  box-shadow: inset 2px 0 0 var(--group-color);
  background: var(--glass-bg-2);
}

.source-row.disabled {
  opacity: 0.7;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.preview-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.span-full {
  grid-column: 1 / -1;
}

.preview-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 14px;
}

.preview-meta-tile {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--glass-border-3);
  border-radius: var(--radius-md);
  background: var(--glass-bg-3);
}

.preview-meta-label {
  color: var(--text-muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.preview-meta-value {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  word-break: break-word;
}

.preview-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--error-soft);
  color: var(--error);
  font-size: 12px;
}

.preview-output {
  min-height: 240px;
  margin-top: 12px;
  padding: 14px 16px;
  border: 1px solid var(--glass-border-3);
  border-radius: var(--radius-md);
  background: var(--glass-bg-3);
  backdrop-filter: blur(var(--glass-blur-3)) saturate(var(--glass-saturate));
  -webkit-backdrop-filter: blur(var(--glass-blur-3)) saturate(var(--glass-saturate));
  color: var(--text-primary);
  transition: opacity var(--transition);
}

html.dark .preview-output {
  background: rgba(8, 10, 20, 0.6);
  color: #e4e7eb;
}

.preview-output.loading {
  opacity: 0.72;
}

.preview-output pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-sans);
  font-size: 12.5px;
  line-height: 1.6;
}

@media (max-width: 1180px) {
  .display-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .section-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .source-tools,
  .token-row {
    justify-content: flex-start;
  }

  .source-row,
  .preview-form {
    grid-template-columns: 1fr;
  }

  .preview-meta {
    grid-template-columns: 1fr;
  }

  .row-actions {
    justify-content: flex-end;
  }
}
</style>
