<script setup lang="ts">
import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch, type PropType } from "vue";
import PageHero from "@/components/PageHero.vue";
import StatTile from "@/components/StatTile.vue";
import SegmentedTabs from "@/components/SegmentedTabs.vue";
import GlassCard from "@/components/GlassCard.vue";
import FormGrid from "@/components/FormGrid.vue";
import FormField from "@/components/FormField.vue";
import {
  libraryApi,
  pan115HelperApi,
  type LibraryCategory,
  type LibraryConfig,
  type LibraryEnrichStats,
  type LibraryFacets,
  type LibraryFile,
  type LibraryLibInfo,
  type LibraryStatus,
  type LibraryTransferTask,
  type LibraryWork,
  type TmdbDetail,
} from "@/api";
import { formatBytes } from "@/utils/format";
import { useGlobalState } from "@/composables/useGlobalState";

const { notifySuccess, notifyError, confirm } = useGlobalState();

type LibraryTab = "browse" | "config";
const tabsList: Array<{ key: LibraryTab; label: string; icon: string }> = [
  { key: "browse", label: "搜索浏览", icon: "mdi-magnify" },
  { key: "config", label: "影库设置", icon: "mdi-cog-outline" },
];
const tab = ref<LibraryTab>("browse");

// ===== 访问令牌：明文只回本机；远程浏览器可手动粘贴一次（存 localStorage） =====
const TOKEN_STORAGE_KEY = "libraryApiToken";
const apiToken = ref(localStorage.getItem(TOKEN_STORAGE_KEY) || "");

function rememberToken(value: string) {
  apiToken.value = value;
  if (value) localStorage.setItem(TOKEN_STORAGE_KEY, value);
  else localStorage.removeItem(TOKEN_STORAGE_KEY);
}

// ===== 影库设置 =====
const config = ref<LibraryConfig | null>(null);
const configTransferConcurrency = ref(5);
const importing = ref(false);
const importingDir = ref(false);
const sources = ref<LibraryLibInfo[]>([]);
const configTokenInput = ref("");
const configClearToken = ref(false);
const configSaving = ref(false);
const pickingFolder = ref(false);
const uploading = ref(false);
const libraryFileInput = ref<HTMLInputElement | null>(null);
const exportDirInput = ref("");
const videoExtensionsInput = ref("");
const openingExportDir = ref(false);

interface DesktopBridge {
  pickFolder?: (payload?: { title?: string }) => Promise<{ cancelled?: boolean; path?: string }>;
  pickFiles?: (payload?: { title?: string; filters?: Array<{ name: string; extensions: string[] }> }) => Promise<{ cancelled?: boolean; paths?: string[] }>;
}
function desktopBridge(): DesktopBridge | undefined {
  return (window as unknown as { cloud123?: DesktopBridge }).cloud123;
}

async function pickFolder(title: string): Promise<string | null> {
  const bridge = desktopBridge();
  if (!bridge?.pickFolder) {
    notifyError("浏览器模式不支持原生选目录，请直接输入路径");
    return null;
  }
  pickingFolder.value = true;
  try {
    const result = await bridge.pickFolder({ title });
    return result?.path || null;
  } finally {
    pickingFolder.value = false;
  }
}

async function loadConfig() {
  const data = await libraryApi.getConfig();
  config.value = data.config;
  configTransferConcurrency.value = data.config.transferConcurrency || 5;
  exportDirInput.value = data.config.exportDir || "";
  videoExtensionsInput.value = data.config.videoExtensions || "";
  // 本机直接回填明文令牌，重启后一眼可见它还在；留空保存=保留现有令牌
  if (data.config.token) {
    configTokenInput.value = data.config.token;
    rememberToken(data.config.token);
  }
}

async function saveConfig() {
  configSaving.value = true;
  try {
    const data = await libraryApi.putConfig({
      transferConcurrency: Number(configTransferConcurrency.value) || 5,
      exportDir: exportDirInput.value.trim(),
      videoExtensions: videoExtensionsInput.value.trim(),
      token: configTokenInput.value.trim(),
      clearToken: configClearToken.value,
    });
    config.value = data.config;
    if (data.config.token) {
      rememberToken(data.config.token);
      configTokenInput.value = data.config.token;
    } else if (configClearToken.value) {
      rememberToken("");
      configTokenInput.value = "";
    }
    configClearToken.value = false;
    notifySuccess("影库配置已保存");
    await loadStatus();
  } catch (error) {
    notifyError(`保存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    configSaving.value = false;
  }
}

function generateToken() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  configTokenInput.value = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  configClearToken.value = false;
}

async function importLibraryFiles(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (!files.length) return;
  const items = await Promise.all(files.map(async (f) => ({ name: f.name, text: await f.text() })));
  await importFiles(items);
}

async function importFiles(items: Array<{ name: string; text: string }>) {
  uploading.value = true;
  try {
    for (const item of items) {
      try {
        const data = await libraryApi.importFile(item.name, item.text, apiToken.value);
        notifySuccess(`已入库 ${item.name}：新增 ${data.added} 个作品 · 重复跳过 ${data.skipped} 个`);
      } catch (error) {
        notifyError(`${item.name} 入库失败：${error instanceof Error ? error.message : String(error)}`);
      }
    }
    await Promise.all([loadStatus(), loadCategories(), loadSources(), loadFacets(), loadEnrich()]);
  } finally {
    uploading.value = false;
  }
}

async function pickAndImportFiles() {
  const bridge = desktopBridge();
  if (bridge?.pickFiles) {
    const result = await bridge.pickFiles({ title: "选择影库文件（可多选）" });
    if (!result?.paths?.length) return;
    importing.value = true;
    try {
      const data = await libraryApi.importPaths(result.paths, apiToken.value);
      notifySuccess(`已导入 ${data.results.length} 个文件：新增 ${data.added} 个作品 · 重复跳过 ${data.skipped} 个${data.failed ? ` · 失败 ${data.failed}` : ""}`);
      await Promise.all([loadStatus(), loadCategories(), loadSources(), loadFacets(), loadEnrich()]);
    } catch (error) {
      notifyError(`导入失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      importing.value = false;
    }
    return;
  }
  libraryFileInput.value?.click();
}

async function importFromFolder() {
  const path = await pickFolder("选择影库文件夹（一次性批量导入）");
  if (!path) return;
  importingDir.value = true;
  try {
    const data = await libraryApi.importDir(path, apiToken.value);
    notifySuccess(`已从文件夹导入 ${data.total} 个文件：新增 ${data.added} 个作品 · 重复跳过 ${data.skipped} 个${data.failed ? ` · 失败 ${data.failed}` : ""}`);
    await Promise.all([loadStatus(), loadCategories(), loadSources(), loadFacets(), loadEnrich()]);
  } catch (error) {
    notifyError(`批量导入失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    importingDir.value = false;
  }
}

async function deleteSource(name: string) {
  const ok = await confirm(`删除影库来源「${name}」？其下所有作品与文件会一并移除。`, "删除影库来源");
  if (!ok) return;
  try {
    await libraryApi.deleteSource(name, apiToken.value);
    notifySuccess(`已删除 ${name}`);
    await Promise.all([loadStatus(), loadCategories(), loadSources(), loadFacets(), loadEnrich()]);
  } catch (error) {
    notifyError(`删除失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function pickExportDir() {
  const path = await pickFolder("选择导出目录（选择后立即固定）");
  if (!path) return;
  exportDirInput.value = path;
  configSaving.value = true;
  try {
    await libraryApi.putConfig({
      transferConcurrency: Number(configTransferConcurrency.value) || 5,
      exportDir: path,
      token: configTokenInput.value.trim(),
      clearToken: false,
    });
    notifySuccess(`导出目录已固定：${path}`);
  } catch (error) {
    notifyError(`保存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    configSaving.value = false;
  }
}

async function openExportDir() {
  openingExportDir.value = true;
  try {
    const data = await libraryApi.openExportDir(exportDirInput.value.trim(), apiToken.value);
    notifySuccess(`已打开：${data.path}`);
  } catch (error) {
    notifyError(`打开目录失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    openingExportDir.value = false;
  }
}

// ===== 状态 =====
const status = ref<LibraryStatus | null>(null);
const libs = ref<LibraryLibInfo[]>([]);
const scanning = ref(false);
let statusTimer: number | undefined;

async function loadStatus() {
  try {
    const data = await libraryApi.status();
    status.value = data.status;
    libs.value = data.libs || [];
    sources.value = data.libs || [];
  } catch {
    /* 静默 */
  }
}

async function loadSources() {
  try {
    sources.value = (await libraryApi.sources()).sources || [];
  } catch {
    /* 静默 */
  }
}

async function refreshLibrary() {
  scanning.value = true;
  try {
    await Promise.all([loadStatus(), loadCategories(), loadSources(), loadFacets(), loadEnrich(), searchWorks()]);
  } finally {
    scanning.value = false;
  }
}

// ===== 影库多选筛选 =====
const libFilter = ref<string[]>([]);
watch(libFilter, () => {
  page.value = 1;
  void loadCategories();
  void loadFacets();
  void loadEnrich();
  void searchWorks();
});

// ===== 分类与搜索 =====
const categories = ref<LibraryCategory[]>([]);
const keyword = ref("");
const searching = ref(false);
const works = ref<LibraryWork[]>([]);
const totalWorks = ref(0);
const page = ref(1);
const pageSize = ref(20);
const searchNotice = ref("");

// ===== 分类维度筛选（对标优爱腾：频道/类型/地区/年代） =====
const facets = ref<LibraryFacets>({
  channels: [], genres: [], regions: [], languages: [], statuses: [],
  resolutions: [], editions: [], decades: [], ratings: [],
});
const activeMedia = ref("");       // "" | movie | tv
const activeGenre = ref("");       // 类型/题材中文名（单选）
const activeRegion = ref("");      // 地区中文桶
const activeDecade = ref<number | "">(""); // 起始年（如 2020 表示 2020s）
const activeLanguage = ref("");    // 语言中文桶
const activeStatus = ref("");      // 剧集更新状态
const activeResolution = ref("");  // 分辨率 4K/1080p/720p/SD
const activeEdition = ref("");     // 片源版本 REMUX/BluRay/WEB-DL/...
const activeRating = ref(0);       // 评分下限 9/8/7，0=不限
const sortMode = ref("");          // "" 默认 / popularity 热度 / rating 评分 / title 片名 / recent 最新入库

// ===== TMDB 分类信息充实进度 =====
const enrich = ref<LibraryEnrichStats | null>(null);
const enriching = ref(false);
let enrichTimer: number | undefined;

// ===== 详情弹层 =====
const detailOpen = ref(false);
const detailWork = ref<LibraryWork | null>(null);

const activeFilterCount = computed(
  () => [activeMedia.value, activeGenre.value, activeRegion.value, activeLanguage.value,
    activeStatus.value, activeResolution.value, activeEdition.value].filter(Boolean).length
    + (activeDecade.value === "" ? 0 : 1)
    + (activeRating.value > 0 ? 1 : 0),
);
const hasBrowseFilters = computed(() => activeFilterCount.value > 0);

const CHANNEL_LABEL: Record<string, string> = { movie: "电影", tv: "剧集" };
function channelLabel(k: string | number): string {
  return CHANNEL_LABEL[String(k)] || String(k);
}
function decadeLabel(k: string | number): string {
  return `${k}年代`;
}
const RATING_LABEL: Record<string, string> = { "9": "9分以上", "8": "8分以上", "7": "7分以上" };
function ratingLabel(k: string | number): string {
  return RATING_LABEL[String(k)] || `${k}分以上`;
}

// 搜索历史
const HISTORY_KEY = "librarySearchHistory";
const history = ref<string[]>((() => {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
})());

function recordHistory(keywordText: string) {
  const q = keywordText.trim();
  if (q.length < 2) return;
  history.value = [q, ...history.value.filter((item) => item !== q)].slice(0, 8);
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value));
  } catch {
    /* 忽略 */
  }
}

function clearHistory() {
  history.value = [];
  localStorage.removeItem(HISTORY_KEY);
}

// 分类维度折叠（每个维度超过一行时收起）
const showAll = ref<Record<string, boolean>>({});
const FACET_LIMIT = 14;
function facetVisible(key: string, len: number) {
  return showAll.value[key] ? len : Math.min(len, FACET_LIMIT);
}

async function loadCategories() {
  try {
    const data = await libraryApi.categories(libFilter.value.join(","));
    categories.value = data.categories || [];
  } catch (error) {
    searchNotice.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadFacets() {
  try {
    const data = await libraryApi.facets({
      mediaType: activeMedia.value,
      genre: activeGenre.value,
      region: activeRegion.value,
      decade: activeDecade.value === "" ? 0 : Number(activeDecade.value),
      language: activeLanguage.value,
      status: activeStatus.value,
      resolution: activeResolution.value,
      edition: activeEdition.value,
      rating: activeRating.value,
      lib: libFilter.value.join(","),
      q: keyword.value.trim(),
      token: apiToken.value,
    });
    facets.value = data.facets;
  } catch {
    /* facets 失败不打断浏览 */
  }
}

async function loadEnrich() {
  try {
    enrich.value = (await libraryApi.enrichStatus(apiToken.value)).stats;
  } catch {
    /* 静默 */
  }
}

async function runEnrich() {
  if (enriching.value) return;
  enriching.value = true;
  try {
    const data = await libraryApi.enrichStart(apiToken.value);
    enrich.value = data.stats;
    notifySuccess(`已整理 ${data.processed} 条分类信息`);
    await Promise.all([loadFacets(), searchWorks()]);
  } catch (error) {
    notifyError(`整理失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    enriching.value = false;
  }
}

async function resetEnrich() {
  const ok = await confirm("重新整理全部作品的分类信息？会重新拉取 TMDB 覆盖现有分类。", "刷新影库分类");
  if (!ok) return;
  try {
    const data = await libraryApi.enrichReset(apiToken.value, true);
    enrich.value = data.stats;
    notifySuccess(`已重排 ${data.requeued} 条，正在后台整理…`);
  } catch (error) {
    notifyError(`重置失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

// 维度选择（单选切换；选中即刷新列表与其它维度候选）
function toggleMedia(v: string) { activeMedia.value = activeMedia.value === v ? "" : v; onFilterChange(); }
function toggleGenre(v: string) { activeGenre.value = activeGenre.value === v ? "" : v; onFilterChange(); }
function toggleRegion(v: string) { activeRegion.value = activeRegion.value === v ? "" : v; onFilterChange(); }
function toggleDecade(v: number) { activeDecade.value = activeDecade.value === v ? "" : v; onFilterChange(); }
function toggleLanguage(v: string) { activeLanguage.value = activeLanguage.value === v ? "" : v; onFilterChange(); }
function toggleStatus(v: string) { activeStatus.value = activeStatus.value === v ? "" : v; onFilterChange(); }
function toggleResolution(v: string) { activeResolution.value = activeResolution.value === v ? "" : v; onFilterChange(); }
function toggleEdition(v: string) { activeEdition.value = activeEdition.value === v ? "" : v; onFilterChange(); }
function toggleRating(v: number) { activeRating.value = activeRating.value === v ? 0 : v; onFilterChange(); }
function clearFilters() {
  activeMedia.value = "";
  activeGenre.value = "";
  activeRegion.value = "";
  activeDecade.value = "";
  activeLanguage.value = "";
  activeStatus.value = "";
  activeResolution.value = "";
  activeEdition.value = "";
  activeRating.value = 0;
  onFilterChange();
}
function onFilterChange() {
  page.value = 1;
  void searchWorks();
  void loadFacets();
}

async function searchWorks(record = false) {
  searching.value = true;
  searchNotice.value = "";
  try {
    const data = await libraryApi.search({
      q: keyword.value.trim(),
      mediaType: activeMedia.value,
      genre: activeGenre.value,
      region: activeRegion.value,
      decade: activeDecade.value === "" ? 0 : Number(activeDecade.value),
      language: activeLanguage.value,
      status: activeStatus.value,
      edition: activeEdition.value,
      rating: activeRating.value,
      sort: sortMode.value,
      page: page.value,
      size: pageSize.value,
      lib: libFilter.value.join(","),
      token: apiToken.value,
    });
    works.value = data.dirs || [];
    totalWorks.value = data.total;
    if (data.total === 0) {
      searchNotice.value = keyword.value.trim()
        ? "没有找到匹配的片名"
        : hasBrowseFilters.value
          ? "当前分类组合下没有作品"
          : "影库还是空的，先去「导入影库」导入影库文件。";
    }
    if (record && keyword.value.trim()) recordHistory(keyword.value);
  } catch (error) {
    works.value = [];
    totalWorks.value = 0;
    searchNotice.value = error instanceof Error ? error.message : String(error);
  } finally {
    searching.value = false;
  }
}

const totalPages = computed(() => Math.max(1, Math.ceil(totalWorks.value / pageSize.value)));
const browseMode = computed(() => !keyword.value.trim());

const SORT_LABEL: Record<string, string> = { "": "默认（年份↓）", popularity: "热度↓", rating: "评分↓", title: "片名 A→Z", recent: "最新入库" };
const sortOptions = ["", "popularity", "rating", "title", "recent"].map((v) => ({ title: SORT_LABEL[v], value: v }));

const resultInfo = computed(() => {
  if (!totalWorks.value) return "";
  if (!browseMode.value) return `片名 “${keyword.value.trim()}” 共找到 ${totalWorks.value.toLocaleString()} 个作品`;
  const parts = [
    activeMedia.value ? channelLabel(activeMedia.value) : "",
    activeGenre.value,
    activeRegion.value,
    activeLanguage.value,
    activeStatus.value,
    activeResolution.value,
    activeEdition.value,
    activeDecade.value === "" ? "" : decadeLabel(activeDecade.value),
    activeRating.value > 0 ? ratingLabel(activeRating.value) : "",
  ].filter(Boolean);
  return `${parts.length ? parts.join(" · ") : "全部"} 共 ${totalWorks.value.toLocaleString()} 个作品 · ${SORT_LABEL[sortMode.value]}`;
});

// 详情弹层：点击海报打开，展开文件列表 + TMDB 详情（评分/类型/简介）
function openDetail(work: LibraryWork) {
  detailWork.value = work;
  detailOpen.value = true;
  void toggleWorkFiles(work);
}
function closeDetail() {
  detailOpen.value = false;
  detailWork.value = null;
}

function goToPage(next: number) {
  if (next < 1 || next > totalPages.value) return;
  page.value = next;
  void searchWorks();
}

function runSearch() {
  page.value = 1;
  void searchWorks(true);
  void loadFacets();
}

let searchDebounce: number | undefined;
watch(keyword, () => {
  window.clearTimeout(searchDebounce);
  searchDebounce = window.setTimeout(() => {
    page.value = 1;
    void searchWorks(true);
    void loadFacets();
  }, 300);
});
watch(sortMode, () => {
  page.value = 1;
  void searchWorks();
});

// ===== TMDB 海报/详情：后端代理（客户端投稿配置的 TMDB TOKEN），带 sqlite 缓存 =====
const POSTER_CACHE_KEY = "libraryPosterCacheV3";
const posterCache: Record<string, string> = (() => {
  try {
    const stored = JSON.parse(localStorage.getItem(POSTER_CACHE_KEY) || "{}") || {};
    return Object.fromEntries(Object.entries(stored).filter(([, v]) => Boolean(v))) as Record<string, string>;
  } catch {
    return {};
  }
})();

async function fetchPoster(work: LibraryWork): Promise<string> {
  const cacheKey = `${work.tmdbId || ""}|${work.title}|${work.year || ""}`;
  if (posterCache[cacheKey] !== undefined) return posterCache[cacheKey];
  if (!work.tmdbId) return "";
  try {
    const data = await libraryApi.poster(work.tmdbId, work.title, work.year || 0, apiToken.value);
    const url = String(data.url || "");
    if (url) {
      posterCache[cacheKey] = url;
      try {
        localStorage.setItem(POSTER_CACHE_KEY, JSON.stringify(posterCache));
      } catch {
        /* 忽略 */
      }
    }
    return url;
  } catch {
    return "";
  }
}

async function fetchTmdbDetail(work: LibraryWork): Promise<TmdbDetail | null> {
  if (!work.tmdbId) return null;
  try {
    const data = await libraryApi.tmdbDetail(work.tmdbId, work.title, work.year || 0, apiToken.value);
    return data.detail || null;
  } catch {
    return null;
  }
}

// 作品海报组件：失败/无图时回退为类型 emoji 占位（剧集📺/电影🎬）
const TV_CATS = ["电视剧", "剧集", "动漫", "短剧"];
const WorkPoster = defineComponent({
  name: "WorkPoster",
  props: {
    work: { type: Object as PropType<LibraryWork>, required: true },
    fetchPoster: { type: Function as PropType<(work: LibraryWork) => Promise<string>>, required: true },
  },
  data() {
    return { url: "" as string, failed: false };
  },
  computed: {
    emoji(): string {
      const first = this.work.dir.split("/")[0];
      return TV_CATS.includes(first) ? "📺" : "🎬";
    },
  },
  watch: {
    "work.dir": {
      immediate: true,
      handler() {
        this.failed = false;
        this.url = "";
        this.fetchPoster(this.work).then((url) => {
          if (url) this.url = url;
        });
      },
    },
  },
  render() {
    if (this.url && !this.failed) {
      return h("img", {
        class: "work-poster",
        src: this.url,
        loading: "lazy",
        alt: this.work.title,
        onError: () => {
          this.failed = true;
        },
      });
    }
    return h("div", { class: "work-poster work-poster-fallback" }, [h("span", this.emoji)]);
  },
});

// ===== 行内文件展开：版本分组 / 集数识别 / 勾选导出与转存 =====
function versionLabel(fileName: string): string {
  if (!fileName) return "默认";
  const tags: string[] = [];
  const res = fileName.match(/(\d{3,4}p|4K)/i);
  if (res) tags.push(res[1].toUpperCase());
  if (/SDR/i.test(fileName)) tags.push("SDR");
  else if (/HDR/i.test(fileName)) tags.push("HDR");
  if (/DoVi|Dolby.?Vision/i.test(fileName)) tags.push("DV");
  if (/H\.?265|HEVC/i.test(fileName)) tags.push("H265");
  else if (/H\.?264|AVC/i.test(fileName)) tags.push("H264");
  else if (/AV1/i.test(fileName)) tags.push("AV1");
  return tags.length ? tags.join(" ") : "默认";
}

function episodeNum(fileName: string): number | null {
  const patterns = [
    /第\s*0*(\d{1,3})\s*[集話话回]/,
    /[Ee][Pp]?\s*0*(\d{1,3})/,
    /\[\s*0*(\d{1,3})\s*\]/,
    /[\s_]0*(\d{1,3})\s*[集話话回]/,
    /[\s._]0*(\d{1,3})\.(mp4|mkv|avi|rmvb|ts|mov)/i,
    /[\s._]0*(\d{2,3})$/,
  ];
  for (const pattern of patterns) {
    const m = fileName.match(pattern);
    if (m) return parseInt(m[1], 10);
  }
  return null;
}

const expandedDir = ref<string | null>(null);
const dirFiles = ref<LibraryFile[]>([]);
const dirFilesLoading = ref(false);
const exporting = ref("");
const exportedDir = ref("");
const detailSelected = ref<Set<string>>(new Set());
const detailVersion = ref("__all__");
const detailInfo = ref<TmdbDetail | null>(null);
const detailVersions = computed(() => {
  const versions = new Set(dirFiles.value.map((f) => versionLabel(f.fileName)));
  return versions.size > 1 ? Array.from(versions) : [];
});
const detailVisibleFiles = computed(() =>
  detailVersion.value === "__all__"
    ? dirFiles.value
    : dirFiles.value.filter((f) => versionLabel(f.fileName) === detailVersion.value),
);

function toggleDetailFile(file: LibraryFile) {
  const next = new Set(detailSelected.value);
  const key = file.path || file.fileName;
  if (next.has(key)) next.delete(key);
  else next.add(key);
  detailSelected.value = next;
}

function toggleDetailAll() {
  const visible = detailVisibleFiles.value.map((f) => f.path || f.fileName);
  const allSelected = visible.length > 0 && visible.every((k) => detailSelected.value.has(k));
  if (allSelected) {
    detailSelected.value = new Set(Array.from(detailSelected.value).filter((k) => !visible.includes(k)));
  } else {
    detailSelected.value = new Set([...detailSelected.value, ...visible]);
  }
}

async function toggleWorkFiles(work: LibraryWork) {
  expandedDir.value = work.dir;
  dirFiles.value = [];
  detailSelected.value = new Set();
  detailVersion.value = "__all__";
  detailInfo.value = work.tmdbStatus === "ok" && work.overview
    ? { title: work.title, year: work.year || 0, overview: work.overview, voteAverage: work.voteAverage, genres: work.genres, posterUrl: work.posterPath }
    : null;
  dirFilesLoading.value = true;
  void fetchTmdbDetail(work).then((detail) => {
    if (detail && expandedDir.value === work.dir) detailInfo.value = detail;
  });
  try {
    const data = await libraryApi.files(work.dir, apiToken.value);
    dirFiles.value = data.files || [];
  } catch (error) {
    notifyError(`读取文件列表失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    dirFilesLoading.value = false;
  }
}

async function exportWork(work: LibraryWork) {
  exporting.value = work.dir;
  try {
    const data = await libraryApi.exportSave({
      dir: work.dir,
      label: work.title || "作品",
      token: apiToken.value,
    });
    exportedDir.value = work.dir;
    notifySuccess(`已导出：${data.file} · ${data.totalFilesCount.toLocaleString()} 个文件 · ${data.formattedTotalSize}`);
    window.setTimeout(() => {
      if (exportedDir.value === work.dir) exportedDir.value = "";
    }, 2500);
  } catch (error) {
    notifyError(`导出失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    exporting.value = "";
  }
}

function exportLabelWithEpisodes(title: string, fileNames: string[]): string {
  const eps = fileNames
    .map((name) => episodeNum(name))
    .filter((ep): ep is number => ep != null)
    .sort((a, b) => a - b);
  if (!eps.length) return title;
  if (eps.length === 1) return `${title}-第${eps[0]}集`;
  const contiguous = eps.every((ep, i) => i === 0 || ep === eps[i - 1] + 1);
  if (contiguous) return `${title}-第${eps[0]}-${eps[eps.length - 1]}集`;
  return `${title}-第${eps.join(",")}集`;
}

async function exportSelectedFiles(work: LibraryWork) {
  const selected = dirFiles.value.filter((f) => detailSelected.value.has(f.path || f.fileName));
  if (!selected.length) {
    notifyError("请先勾选要导出的文件");
    return;
  }
  exporting.value = work.dir;
  try {
    const data = await libraryApi.exportSave({
      dir: work.dir,
      includeFiles: selected.map((f) => f.path || f.fileName),
      label: exportLabelWithEpisodes(work.title || "作品", selected.map((f) => f.fileName)),
      token: apiToken.value,
    });
    exportedDir.value = work.dir;
    notifySuccess(`已导出：${data.file} · ${data.totalFilesCount.toLocaleString()} 个文件`);
    window.setTimeout(() => {
      if (exportedDir.value === work.dir) exportedDir.value = "";
    }, 2500);
  } catch (error) {
    notifyError(`导出失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    exporting.value = "";
  }
}

// ===== 客户端内转存到 123 云盘 =====
const TRANSFER_TARGET_KEY = "libraryTransferTarget";
const transferTargetPath = ref(localStorage.getItem(TRANSFER_TARGET_KEY) || "影库转存");
watch(transferTargetPath, (value) => {
  const trimmed = value.trim();
  if (trimmed) localStorage.setItem(TRANSFER_TARGET_KEY, trimmed);
});

const transferPickerOpen = ref(false);
const transferPickerLoading = ref(false);
const transferPickerPath = ref<Array<{ fileId: number; name: string }>>([]);
const transferPickerDirs = ref<Array<{ fileId: number; name: string }>>([]);
const transferBusy = ref("");
const transferTask = ref<LibraryTransferTask | null>(null);
let transferTimer: number | undefined;

async function loadTransferPicker(parentId = 0) {
  transferPickerLoading.value = true;
  try {
    const data = await pan115HelperApi.pan123Browse(parentId);
    transferPickerDirs.value = data.directories || [];
  } catch (error) {
    notifyError(`读取 123 目录失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    transferPickerLoading.value = false;
  }
}

function openTransferPicker() {
  transferPickerPath.value = [];
  transferPickerOpen.value = true;
  void loadTransferPicker(0);
}

function pickerEnterDir(dir: { fileId: number; name: string }) {
  transferPickerPath.value.push(dir);
  void loadTransferPicker(dir.fileId);
}

function pickerGoTo(index: number) {
  transferPickerPath.value = transferPickerPath.value.slice(0, index + 1);
  void loadTransferPicker(transferPickerPath.value[index].fileId);
}

function pickerGoRoot() {
  transferPickerPath.value = [];
  void loadTransferPicker(0);
}

function pickerConfirm() {
  transferTargetPath.value = "/" + transferPickerPath.value.map((i) => i.name).join("/");
  transferPickerOpen.value = false;
}

const transferSpeed = computed(() => {
  const task = transferTask.value;
  if (!task || !task.progress.startedAt || !task.progress.done) return { rate: "", eta: "" };
  const elapsed = Math.max(0.5, Date.now() / 1000 - task.progress.startedAt);
  const rate = task.progress.done / elapsed;
  const remain = Math.max(0, task.progress.total - task.progress.done);
  return {
    rate: rate.toFixed(1),
    eta: rate > 0 ? `${Math.ceil(remain / rate)} 秒` : "",
  };
});

async function startTransfer(worksArg: Array<{ dir: string; includeFiles?: string[] }>, label: string) {
  if (transferBusy.value) return;
  transferBusy.value = label;
  try {
    const data = await libraryApi.transfer(
      worksArg.map((w) => w.dir),
      transferTargetPath.value.trim().replace(/^\//, ""),
      "",
      apiToken.value,
      worksArg.length === 1 ? worksArg[0].includeFiles : undefined,
    );
    notifySuccess(`转存任务已创建（${label}），正在后台秒传…`);
    await pollTransferTask(data.taskId);
  } catch (error) {
    notifyError(`转存失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    transferBusy.value = "";
  }
}

async function submitTransferWork(work: LibraryWork) {
  await startTransfer([{ dir: work.dir }], work.title || work.dir);
}

async function submitTransferSelectedFiles(work: LibraryWork) {
  const selected = dirFiles.value.filter((f) => detailSelected.value.has(f.path || f.fileName));
  if (!selected.length) {
    notifyError("请先勾选要转存的文件");
    return;
  }
  await startTransfer(
    [{ dir: work.dir, includeFiles: selected.map((f) => f.path || f.fileName) }],
    `${work.title}（选中 ${selected.length} 个文件）`,
  );
}

async function pollTransferTask(taskId: string) {
  window.clearInterval(transferTimer);
  const tick = async () => {
    try {
      const data = await libraryApi.transferTask(taskId, apiToken.value);
      transferTask.value = data.task;
      if (data.task.status !== "running") {
        window.clearInterval(transferTimer);
        if (data.task.status === "done") notifySuccess(data.task.progress.step);
        else notifyError(data.task.error || data.task.progress.step);
      }
    } catch {
      window.clearInterval(transferTimer);
    }
  };
  await tick();
  transferTimer = window.setInterval(tick, 1500);
}

// ===== 存储分析 =====
const storageRings = computed(() => {
  let offset = 25;
  return storageSlices.value.map((slice) => {
    const ring = { ...slice, dash: slice.percent, offset };
    offset -= slice.percent;
    return ring;
  });
});

const storageSlices = computed(() => {
  const total = categories.value.reduce((sum, c) => sum + (c.size || 0), 0);
  if (!total) return [];
  const palette = ["#5b8def", "#41c6a9", "#f0a24f", "#e86f6f", "#9d7bde", "#5bc0de"];
  const top = categories.value.slice(0, 5);
  const rest = categories.value.slice(5);
  const slices = top.map((c, i) => ({
    name: c.name,
    size: c.size,
    color: palette[i % palette.length],
    percent: (c.size / total) * 100,
  }));
  if (rest.length) {
    const restSize = rest.reduce((sum, c) => sum + (c.size || 0), 0);
    slices.push({ name: "其他", size: restSize, color: "#8a939f", percent: (restSize / total) * 100 });
  }
  return slices;
});

// ===== 页面状态记忆 =====
const UI_STATE_KEY = "libraryUiState";
function saveUiState() {
  try {
    localStorage.setItem(UI_STATE_KEY, JSON.stringify({
      tab: tab.value,
      activeMedia: activeMedia.value,
      activeGenre: activeGenre.value,
      activeRegion: activeRegion.value,
      activeDecade: activeDecade.value,
      activeLanguage: activeLanguage.value,
      activeStatus: activeStatus.value,
      activeResolution: activeResolution.value,
      activeEdition: activeEdition.value,
      activeRating: activeRating.value,
      sortMode: sortMode.value,
      page: page.value,
      keyword: keyword.value,
    }));
  } catch {
    /* 忽略 */
  }
}

function restoreUiState() {
  try {
    const state = JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}") || {};
    if (["browse", "config"].includes(state.tab)) tab.value = state.tab;
    activeMedia.value = String(state.activeMedia || "");
    activeGenre.value = String(state.activeGenre || "");
    activeRegion.value = String(state.activeRegion || "");
    activeDecade.value = state.activeDecade === "" || state.activeDecade == null ? "" : Number(state.activeDecade);
    activeLanguage.value = String(state.activeLanguage || "");
    activeStatus.value = String(state.activeStatus || "");
    activeResolution.value = String(state.activeResolution || "");
    activeEdition.value = String(state.activeEdition || "");
    activeRating.value = Number(state.activeRating || 0);
    sortMode.value = String(state.sortMode || "");
    page.value = Math.max(1, Number(state.page) || 1);
    keyword.value = String(state.keyword || "");
  } catch {
    /* 忽略 */
  }
}

watch(
  [tab, activeMedia, activeGenre, activeRegion, activeDecade, activeLanguage, activeStatus,
    activeResolution, activeEdition, activeRating, sortMode, page, keyword],
  saveUiState,
);

onMounted(async () => {
  restoreUiState();
  await Promise.all([
    loadConfig(), loadStatus(), loadCategories(), loadSources(),
    loadFacets(), loadEnrich(), searchWorks(),
  ]);
  statusTimer = window.setInterval(loadStatus, 30000);
  enrichTimer = window.setInterval(loadEnrich, 8000);
});

onUnmounted(() => {
  window.clearInterval(statusTimer);
  window.clearInterval(enrichTimer);
  window.clearInterval(transferTimer);
  window.clearTimeout(searchDebounce);
});
</script>

<template>
  <div class="page">
    <PageHero
      title="影库"
      desc="导入影库文件（支持 123 助手全部格式）解析入本地数据库：搜索浏览、导出秒传、一键转存 123 云盘，并开放接口给油猴脚本随时随地搜索转存。"
      icon="mdi-movie-open-outline"
      group="dashboard"
    />

    <SegmentedTabs v-model="tab" :tabs="tabsList" />

    <!-- ====================== 搜索浏览 ====================== -->
    <div v-show="tab === 'browse'" class="section-stack">
      <div class="stat-grid">
        <StatTile label="来源数量" :value="status?.libCount ?? 0" icon="mdi-database-outline" tone="info" />
        <StatTile label="作品数" :value="status?.workCount ?? 0" icon="mdi-movie-open-outline" tone="group" />
        <StatTile label="文件数" :value="status?.fileCount ?? 0" icon="mdi-file-multiple-outline" tone="info" />
        <StatTile label="总大小" :value="status?.totalSizeLabel || '0 B'" icon="mdi-harddisk" tone="success" />
      </div>

      <GlassCard accent="group" icon="mdi-magnify" title="海报墙" desc="按 TMDB 分类（频道 / 类型 / 地区 / 年代）筛选浏览；点击海报查看详情、导出或转存。片名搜索仍可用。">
        <div class="search-row">
          <v-select
            v-model="libFilter"
            :items="libs.map((l) => l.name)"
            label="影库筛选"
            multiple
            clearable
            variant="outlined"
            density="compact"
            hide-details
            class="lib-select"
          />
          <v-text-field
            v-model="keyword"
            label="片名关键词"
            placeholder="输入片名，例如：海王 流浪地球 三体"
            variant="outlined"
            density="compact"
            clearable
            class="grow"
            @keyup.enter="runSearch"
          />
          <v-btn color="primary" prepend-icon="mdi-magnify" :loading="searching" @click="runSearch">搜索</v-btn>
          <v-btn variant="outlined" prepend-icon="mdi-refresh" :loading="scanning" @click="refreshLibrary">刷新</v-btn>
        </div>

        <div v-if="!keyword.trim() && history.length" class="history-row">
          <span class="muted-hint">历史：</span>
          <button v-for="item in history" :key="item" type="button" class="cat-chip" @click="keyword = item">{{ item }}</button>
          <v-btn size="x-small" variant="text" @click="clearHistory">清空</v-btn>
        </div>

        <div class="transfer-row">
          <v-text-field
            v-model="transferTargetPath"
            label="转存目标目录（自动逐级创建）"
            variant="outlined"
            density="compact"
            hide-details
            class="grow"
          />
          <v-btn variant="outlined" prepend-icon="mdi-folder-open-outline" @click="openTransferPicker">浏览 123 目录…</v-btn>
        </div>

        <div class="filter-panel">
          <div class="filter-head">
            <span class="filter-title">分类筛选</span>
            <v-select
              v-model="sortMode"
              :items="sortOptions"
              item-title="title"
              item-value="value"
              label="排序"
              variant="outlined"
              density="compact"
              hide-details
              class="sort-select"
            />
            <v-spacer />
            <span v-if="enrich" class="enrich-hint">
              分类信息：已整理 {{ enrich.ok }} · 待整理 {{ enrich.pending + enrich.failed }}<template v-if="enrich.failed">（失败 {{ enrich.failed }}）</template>
            </span>
            <v-btn size="small" variant="text" :loading="enriching" @click="runEnrich">立即整理</v-btn>
            <v-btn size="small" variant="text" @click="resetEnrich">刷新全部</v-btn>
            <v-btn v-if="hasBrowseFilters" size="small" color="primary" variant="text" prepend-icon="mdi-close" @click="clearFilters">清除筛选</v-btn>
          </div>

          <div v-if="facets.channels.length" class="facet-row">
            <span class="facet-label">频道</span>
            <div class="facet-chips">
              <button
                v-for="c in facets.channels"
                :key="c.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeMedia === String(c.name) }"
                @click="toggleMedia(String(c.name))"
              >{{ channelLabel(c.name) }}<small>{{ c.count }}</small></button>
            </div>
          </div>

          <div v-if="facets.genres.length" class="facet-row">
            <span class="facet-label">类型</span>
            <div class="facet-chips">
              <button
                v-for="g in facets.genres.slice(0, facetVisible('genres', facets.genres.length))"
                :key="g.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeGenre === String(g.name) }"
                @click="toggleGenre(String(g.name))"
              >{{ g.name }}<small>{{ g.count }}</small></button>
              <v-btn v-if="facets.genres.length > FACET_LIMIT" size="x-small" variant="text" @click="showAll.genres = !showAll.genres">
                {{ showAll.genres ? "收起" : "展开更多" }}
              </v-btn>
            </div>
          </div>

          <div v-if="facets.regions.length" class="facet-row">
            <span class="facet-label">地区</span>
            <div class="facet-chips">
              <button
                v-for="r in facets.regions"
                :key="r.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeRegion === String(r.name) }"
                @click="toggleRegion(String(r.name))"
              >{{ r.name }}<small>{{ r.count }}</small></button>
            </div>
          </div>

          <div v-if="facets.decades.length" class="facet-row">
            <span class="facet-label">年代</span>
            <div class="facet-chips">
              <button
                v-for="d in facets.decades.slice(0, facetVisible('decades', facets.decades.length))"
                :key="d.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeDecade === Number(d.name) }"
                @click="toggleDecade(Number(d.name))"
              >{{ decadeLabel(d.name) }}<small>{{ d.count }}</small></button>
              <v-btn v-if="facets.decades.length > FACET_LIMIT" size="x-small" variant="text" @click="showAll.decades = !showAll.decades">
                {{ showAll.decades ? "收起" : "展开更多" }}
              </v-btn>
            </div>
          </div>

          <div v-if="facets.languages.length" class="facet-row">
            <span class="facet-label">语言</span>
            <div class="facet-chips">
              <button
                v-for="l in facets.languages"
                :key="l.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeLanguage === String(l.name) }"
                @click="toggleLanguage(String(l.name))"
              >{{ l.name }}<small>{{ l.count }}</small></button>
            </div>
          </div>

          <div v-if="facets.statuses.length" class="facet-row">
            <span class="facet-label">状态</span>
            <div class="facet-chips">
              <button
                v-for="s in facets.statuses"
                :key="s.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeStatus === String(s.name) }"
                @click="toggleStatus(String(s.name))"
              >{{ s.name }}<small>{{ s.count }}</small></button>
            </div>
          </div>

          <div v-if="facets.resolutions.length" class="facet-row">
            <span class="facet-label">画质</span>
            <div class="facet-chips">
              <button
                v-for="r in facets.resolutions"
                :key="r.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeResolution === String(r.name) }"
                @click="toggleResolution(String(r.name))"
              >{{ r.name }}<small>{{ r.count }}</small></button>
            </div>
          </div>

          <div v-if="facets.editions.length" class="facet-row">
            <span class="facet-label">版本</span>
            <div class="facet-chips">
              <button
                v-for="e in facets.editions.slice(0, facetVisible('editions', facets.editions.length))"
                :key="e.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeEdition === String(e.name) }"
                @click="toggleEdition(String(e.name))"
              >{{ e.name }}<small>{{ e.count }}</small></button>
              <v-btn v-if="facets.editions.length > FACET_LIMIT" size="x-small" variant="text" @click="showAll.editions = !showAll.editions">
                {{ showAll.editions ? "收起" : "展开更多" }}
              </v-btn>
            </div>
          </div>

          <div v-if="facets.ratings.length" class="facet-row">
            <span class="facet-label">评分</span>
            <div class="facet-chips">
              <button
                v-for="rt in facets.ratings"
                :key="rt.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeRating === Number(rt.name) }"
                @click="toggleRating(Number(rt.name))"
              >{{ ratingLabel(rt.name) }}<small>{{ rt.count }}</small></button>
            </div>
          </div>

          <div v-if="!facets.channels.length && !facets.genres.length && !facets.regions.length && !facets.decades.length && !facets.languages.length && !facets.statuses.length && !facets.resolutions.length && !facets.editions.length && !facets.ratings.length" class="muted-hint">
            还没有可用的分类维度——导入带 {tmdb-N} / [tmdb-N] 标记的影库文件后，点「立即整理」拉取 TMDB 分类即可出现。
          </div>
        </div>

        <div v-if="resultInfo" class="result-info">{{ resultInfo }}</div>
        <div v-if="searchNotice" class="hub-status-line">{{ searchNotice }}</div>

        <div v-if="searching" class="empty-state"><p>加载中…</p></div>
        <div v-else-if="works.length" class="poster-grid">
          <div v-for="work in works" :key="work.dir" class="poster-card" @click="openDetail(work)">
            <div class="poster-box">
              <WorkPoster :work="work" :fetch-poster="fetchPoster" />
              <span v-if="work.mediaType" class="poster-badge">{{ channelLabel(work.mediaType) }}</span>
              <span v-else-if="work.tmdbStatus === 'pending'" class="poster-badge pending">整理中…</span>
              <span v-else-if="!work.tmdbId" class="poster-badge uncategorized">未分类</span>
            </div>
            <div class="poster-title" :title="work.dir">{{ work.title }}</div>
            <div class="poster-sub">{{ work.year || "—" }}</div>
          </div>
        </div>
        <div v-else-if="!searchNotice" class="empty-state"><p>输入片名，或用上方分类维度筛选浏览。</p></div>

        <div v-if="totalPages > 1" class="pager">
          <v-btn size="small" variant="outlined" :disabled="page <= 1" @click="goToPage(page - 1)">上一页</v-btn>
          <span class="pager-info">{{ page }} / {{ totalPages }}（共 {{ totalWorks }} 个）</span>
          <v-btn size="small" variant="outlined" :disabled="page >= totalPages" @click="goToPage(page + 1)">下一页</v-btn>
          <v-select
            v-model="pageSize"
            :items="[20, 50, 100]"
            label="每页"
            variant="outlined"
            density="compact"
            hide-details
            class="size-select"
            @update:model-value="page = 1; searchWorks()"
          />
        </div>
      </GlassCard>

      <GlassCard v-if="storageSlices.length" icon="mdi-chart-donut" title="存储分析" desc="按一级分类统计影库占用（数据来自影库 JSON，与云盘实际占用无关）。">
        <div class="storage-row">
          <svg class="storage-ring" viewBox="0 0 42 42" role="img">
            <circle cx="21" cy="21" r="15.9" fill="none" stroke="rgba(127,127,127,.15)" stroke-width="6" />
            <circle
              v-for="slice in storageRings"
              :key="slice.name"
              cx="21" cy="21" r="15.9" fill="none"
              :stroke="slice.color"
              stroke-width="6"
              :stroke-dasharray="`${slice.dash} 100`"
              :stroke-dashoffset="slice.offset"
            />
            <text x="21" y="20" class="storage-ring-total">{{ status?.totalSizeLabel || "" }}</text>
            <text x="21" y="26" class="storage-ring-sub">{{ status?.workCount ?? 0 }} 个作品</text>
          </svg>
          <div class="storage-legend">
            <div v-for="slice in storageSlices" :key="slice.name" class="storage-legend-item">
              <span class="storage-dot" :style="{ background: slice.color }" />
              <span class="storage-name">{{ slice.name }}</span>
              <span class="storage-size">{{ formatBytes(slice.size) }} · {{ slice.percent.toFixed(1) }}%</span>
            </div>
          </div>
        </div>
      </GlassCard>
    </div>
    <!-- ====================== 影库设置 ====================== -->
    <div v-show="tab === 'config'" class="section-stack">
      <GlassCard accent="group" icon="mdi-cog-outline" title="导入影库" desc="选择影库文件（支持 123 助手全部格式）解析入数据库；源文件之后删掉也不影响查询。">
        <div class="button-row">
          <v-btn color="primary" prepend-icon="mdi-file-multiple-outline" :loading="importing" @click="pickAndImportFiles">选择影库文件（可多选）…</v-btn>
          <v-btn variant="outlined" prepend-icon="mdi-folder-multiple-outline" :loading="importingDir" @click="importFromFolder">从文件夹批量导入…</v-btn>
          <input ref="libraryFileInput" type="file" accept=".json,.txt,.123share,.123fastlink" multiple hidden @change="importLibraryFiles" />
        </div>
        <div class="muted-hint">同一作品重复导入会自动跳过（保留先入库的）；同名来源重新导入会更新其内容。</div>
        <FormField label="转存并发（1-10，保存后生效）">
          <div class="port-row">
            <v-text-field
              v-model.number="configTransferConcurrency"
              type="number"
              variant="outlined"
              density="compact"
              hide-details
              class="small-input"
            />
            <v-btn color="primary" prepend-icon="mdi-content-save" :loading="configSaving" @click="saveConfig">保存</v-btn>
          </div>
        </FormField>
        <FormField hint="用于判定导入 JSON 里的文件条目是否为视频（计入作品视频数）。逗号或空格分隔，如「tp mxf m4v」；与内置默认（mkv/mp4/ts/iso/rmvb 等）合并，修改后重新导入才生效。">
          <div class="port-row">
            <v-text-field
              v-model="videoExtensionsInput"
              label="自定义视频扩展名（可选）"
              placeholder="例：tp, mxf, m4v"
              variant="outlined"
              density="compact"
              hide-details
              class="grow"
            />
          </div>
        </FormField>
        <FormField label="导出目录（导出的秒传 JSON 落在这里；留空=数据目录下的「秒传文件导出」）">
          <div class="port-row">
            <v-text-field
              v-model="exportDirInput"
              label="导出目录"
              variant="outlined"
              density="compact"
              hide-details
              class="grow"
            />
            <v-btn variant="outlined" prepend-icon="mdi-folder-open-outline" @click="pickExportDir">选择并固定…</v-btn>
            <v-btn variant="outlined" prepend-icon="mdi-folder-open-outline" :loading="openingExportDir" @click="openExportDir">打开</v-btn>
          </div>
        </FormField>
      </GlassCard>

      <GlassCard icon="mdi-key-outline" title="访问令牌" desc="开放局域网/外网访问时建议配置：影库搜索、文件、导出与转存接口将要求携带令牌。油猴脚本里填同一串令牌即可。">
        <FormGrid>
          <FormField hint="留空保存 = 保留现有令牌；勾选清除则删除。本机管理页始终能看到明文。">
            <div class="port-row">
              <v-text-field
                v-model="configTokenInput"
                label="访问令牌"
                :placeholder="config?.tokenSet ? `已设置（${config.tokenPreview || '本机可见明文'}）` : '未设置，接口无需令牌'"
                variant="outlined"
                density="compact"
                hide-details
                class="grow"
              />
              <v-btn variant="outlined" prepend-icon="mdi-auto-fix" @click="generateToken">生成</v-btn>
              <v-checkbox v-model="configClearToken" label="清除令牌" hide-details density="compact" />
            </div>
          </FormField>
          <div v-if="config?.tokenSet" class="hub-status-line">
            ✅ 令牌已保存并持久化，重启不会丢失：
            <code>{{ config.token ? config.token : (config.tokenPreview || "（远程模式不显示，留空保存即保留）") }}</code>
          </div>
        </FormGrid>
      </GlassCard>

      <GlassCard v-if="libs.length" icon="mdi-bookshelf" title="已导入影库" desc="每个导入的文件一个来源；删除来源会一并移除其作品与文件（其他来源不受影响）。">
        <div class="hub-offline-list">
          <div v-for="lib in libs" :key="lib.name" class="hub-offline-row">
            <span class="lib-name"><v-icon icon="mdi-file-code-outline" size="16" />{{ lib.name }}</span>
            <span class="share-item-meta">
              {{ lib.fileCount }} 个作品 · {{ formatBytes(lib.totalSize) }} · 导入于 {{ lib.loadDate }}
              <v-btn size="x-small" variant="text" color="error" prepend-icon="mdi-delete-outline" @click="deleteSource(lib.name)">删除</v-btn>
            </span>
          </div>
        </div>
      </GlassCard>
    </div>

    <!-- ====================== 作品详情弹层 ====================== -->
    <v-dialog v-model="detailOpen" max-width="820" scrollable @update:model-value="(v: boolean) => { if (!v) closeDetail(); }">
      <v-card v-if="detailWork" class="dir-picker-card detail-card">
        <div class="detail-head">
          <div class="detail-poster">
            <WorkPoster :work="detailWork" :fetch-poster="fetchPoster" />
          </div>
          <div class="detail-info">
            <div class="detail-title-line">
              <strong class="detail-title">{{ detailWork.title }}</strong>
              <span v-if="detailWork.year" class="work-year">({{ detailWork.year }})</span>
            </div>
            <div class="detail-chips">
              <v-chip v-if="detailWork.mediaType" size="small" color="primary" variant="tonal">{{ channelLabel(detailWork.mediaType) }}</v-chip>
              <v-chip v-if="detailWork.region" size="small" variant="tonal">{{ detailWork.region }}</v-chip>
              <v-chip v-if="detailWork.language" size="small" variant="outlined">{{ detailWork.language }}</v-chip>
              <v-chip v-if="detailWork.airStatus" size="small" variant="outlined">{{ detailWork.airStatus }}</v-chip>
              <v-chip v-if="detailWork.resolution" size="small" variant="outlined">{{ detailWork.resolution }}</v-chip>
              <v-chip v-if="detailWork.edition" size="small" variant="outlined">{{ detailWork.edition }}</v-chip>
              <v-chip v-if="detailInfo && detailInfo.voteAverage" size="small" color="warning" variant="tonal">⭐ {{ detailInfo.voteAverage.toFixed(1) }}</v-chip>
              <v-chip v-if="detailWork.tmdbId" size="small" variant="text" class="tmdb-chip">TMDB:{{ detailWork.tmdbId }}</v-chip>
              <v-chip v-if="!detailWork.tmdbId" size="small" color="grey" variant="tonal">未分类</v-chip>
              <v-chip v-else-if="detailWork.tmdbStatus === 'pending'" size="small" color="grey" variant="tonal">分类整理中…</v-chip>
            </div>
            <div v-if="detailInfo && detailInfo.genres.length" class="detail-genres">
              <v-chip v-for="g in detailInfo.genres" :key="g" size="x-small" variant="outlined">{{ g }}</v-chip>
            </div>
            <div v-if="detailInfo && detailInfo.overview" class="detail-overview">{{ detailInfo.overview }}</div>
            <div class="detail-meta muted-hint">{{ detailWork.videoCount }} 个视频 · {{ detailWork.count }} 个文件 · {{ formatBytes(detailWork.totalSize) }}</div>
            <div class="work-path" :title="detailWork.dir">{{ detailWork.dir }}</div>
            <div class="detail-actions">
              <v-btn size="small" variant="outlined" prepend-icon="mdi-download" :loading="exporting === detailWork.dir" @click="exportWork(detailWork)">导出全部</v-btn>
              <v-btn size="small" color="success" variant="tonal" prepend-icon="mdi-fast-forward" :loading="transferBusy === detailWork.dir" :disabled="Boolean(transferBusy)" @click="submitTransferWork(detailWork)">转存全部</v-btn>
              <v-spacer />
              <v-btn size="small" variant="text" @click="closeDetail">关闭</v-btn>
            </div>
          </div>
        </div>

        <v-divider />

        <v-card-text class="detail-files">
          <div v-if="dirFilesLoading" class="empty-state"><p>文件加载中…</p></div>
          <template v-else>
            <div v-if="detailVersions.length" class="cat-bar">
              <button type="button" class="cat-chip" :class="{ active: detailVersion === '__all__' }" @click="detailVersion = '__all__'">全部 <small>{{ dirFiles.length }}</small></button>
              <button
                v-for="v in detailVersions"
                :key="v"
                type="button"
                class="cat-chip"
                :class="{ active: detailVersion === v }"
                @click="detailVersion = v"
              >{{ v }} <small>{{ dirFiles.filter((f) => versionLabel(f.fileName) === v).length }}</small></button>
            </div>
            <div class="dir-picker-list">
              <label v-for="file in detailVisibleFiles" :key="file.path || file.fileName" class="share-item-row">
                <v-checkbox
                  :model-value="detailSelected.has(file.path || file.fileName)"
                  density="compact"
                  hide-details
                  :label="file.fileName + (episodeNum(file.fileName) != null ? `（第${episodeNum(file.fileName)}集）` : '')"
                  @update:model-value="toggleDetailFile(file)"
                />
                <span class="share-item-meta">
                  <v-chip v-if="detailVersions.length" size="x-small" variant="tonal" class="mr-2">{{ versionLabel(file.fileName) }}</v-chip>
                  {{ formatBytes(file.size) }}{{ file.isVideo ? " · 视频" : "" }}
                </span>
              </label>
            </div>
            <div class="button-row">
              <v-btn size="small" variant="text" @click="toggleDetailAll">
                {{ detailVisibleFiles.length && detailVisibleFiles.every((f) => detailSelected.has(f.path || f.fileName)) ? "全不选" : "全选" }}
              </v-btn>
              <span class="muted-hint">已选 {{ detailSelected.size }} / {{ dirFiles.length }}</span>
              <v-spacer />
              <v-btn size="small" variant="outlined" prepend-icon="mdi-download" :disabled="!detailSelected.size" @click="exportSelectedFiles(detailWork)">导出选中</v-btn>
              <v-btn size="small" color="success" variant="tonal" prepend-icon="mdi-fast-forward" :disabled="!detailSelected.size || Boolean(transferBusy)" @click="submitTransferSelectedFiles(detailWork)">转存选中</v-btn>
            </div>
          </template>
        </v-card-text>
      </v-card>
    </v-dialog>

    <!-- ====================== 123 目录选择弹窗 ====================== -->
    <v-dialog v-model="transferPickerOpen" max-width="620">
      <v-card class="dir-picker-card">
        <v-card-title class="dir-picker-title">
          <v-icon icon="mdi-folder-open-outline" size="22" class="mr-2" />
          选择 123 云盘目标目录
        </v-card-title>
        <v-card-text>
          <div class="dir-picker-breadcrumb">
            <v-btn size="small" variant="text" @click="pickerGoRoot">根目录</v-btn>
            <template v-for="(item, index) in transferPickerPath" :key="item.fileId">
              <v-icon icon="mdi-chevron-right" size="16" />
              <v-btn size="small" variant="text" @click="pickerGoTo(index)">{{ item.name }}</v-btn>
            </template>
          </div>
          <div v-if="transferPickerLoading" class="empty-state"><p>目录加载中…</p></div>
          <div v-else-if="!transferPickerDirs.length" class="empty-state"><p>此目录为空，可直接「选择此目录」。</p></div>
          <div v-else class="dir-picker-list">
            <v-list-item
              v-for="dir in transferPickerDirs"
              :key="dir.fileId"
              :title="dir.name"
              prepend-icon="mdi-folder"
              append-icon="mdi-chevron-right"
              @click="pickerEnterDir(dir)"
            />
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="transferPickerOpen = false">取消</v-btn>
          <v-btn color="primary" :disabled="transferPickerLoading" @click="pickerConfirm">选择此目录</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ====================== 转存进度弹窗 ====================== -->
    <v-dialog :model-value="Boolean(transferTask && transferTask.status === 'running')" max-width="620" persistent>
      <v-card v-if="transferTask" class="dir-picker-card">
        <v-card-title class="dir-picker-title">
          <v-icon icon="mdi-fast-forward" size="22" class="mr-2" />
          正在秒传到 123 云盘…
        </v-card-title>
        <v-card-text>
          <v-progress-linear :model-value="(transferTask.progress.done / Math.max(1, transferTask.progress.total)) * 100" color="primary" height="8" rounded />
          <div class="hub-status-line">{{ transferTask.progress.step }}</div>
          <div class="hub-status-line">
            成功 {{ transferTask.progress.success }} · 未命中 {{ transferTask.progress.missed }} · 失败 {{ transferTask.progress.failed }}
            / 共 {{ transferTask.progress.total }} · 并发 {{ transferTask.progress.concurrency || 1 }}
            <template v-if="transferSpeed.rate"> · {{ transferSpeed.rate }} 个/秒 · 剩余约 {{ transferSpeed.eta }}</template>
          </div>
          <div class="transfer-log">
            <div v-for="(line, index) in transferTask.progress.log.slice(-8)" :key="index">{{ line }}</div>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="libraryApi.transferCancel(transferTask.taskId, apiToken)">取消任务</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}

.section-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.button-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.search-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.lib-select {
  max-width: 240px;
}

.grow {
  flex: 1;
}

.small-input {
  max-width: 150px;
}

.size-select {
  max-width: 110px;
}

.history-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}

.transfer-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.muted-hint {
  color: var(--text-secondary);
  font-size: 12px;
}

.error-text {
  color: rgb(var(--v-theme-error));
}

.filter-panel {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.filter-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.filter-title {
  font-size: 14px;
  font-weight: 700;
}

.sort-select {
  max-width: 170px;
}

.enrich-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.facet-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.facet-label {
  flex-shrink: 0;
  width: 44px;
  font-size: 13px;
  color: var(--text-secondary);
  padding-top: 5px;
}

.facet-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.poster-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(128px, 1fr));
  gap: 16px;
  margin-top: 14px;
}

.poster-card {
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.poster-box {
  position: relative;
  width: 100%;
  aspect-ratio: 2 / 3;
  border-radius: 10px;
  overflow: hidden;
  background: rgba(0, 0, 0, 0.25);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.poster-card:hover .poster-box {
  transform: translateY(-3px);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.35);
}

.poster-box :deep(.work-poster) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 0;
}

.poster-box :deep(.work-poster-fallback) {
  width: 100%;
  height: 100%;
  font-size: 40px;
}

.poster-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  font-size: 11px;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.62);
  color: #fff;
  backdrop-filter: blur(2px);
}

.poster-badge.pending {
  background: rgba(91, 141, 239, 0.85);
}

.poster-badge.uncategorized {
  background: rgba(120, 120, 120, 0.7);
}

.poster-title {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.poster-sub {
  font-size: 12px;
  color: var(--text-secondary);
}

.detail-card {
  overflow: hidden;
}

.detail-head {
  display: flex;
  gap: 18px;
  padding: 18px;
}

.detail-poster {
  width: 160px;
  flex-shrink: 0;
}

.detail-poster :deep(.work-poster) {
  width: 160px;
  height: 240px;
  object-fit: cover;
  border-radius: 10px;
}

.detail-poster :deep(.work-poster-fallback) {
  width: 160px;
  height: 240px;
  font-size: 48px;
}

.detail-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-title-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.detail-title {
  font-size: 20px;
  font-weight: 700;
}

.detail-chips,
.detail-genres {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.detail-overview {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.detail-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.detail-files {
  max-height: 46vh;
}

.cat-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.cat-chip {
  border: 1px solid var(--border);
  background: transparent;
  color: inherit;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 13px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.cat-chip small {
  opacity: 0.6;
  font-size: 11px;
}

.cat-chip.active {
  border-color: transparent;
  background: rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-primary));
}

.cat-chip.sub {
  font-size: 12px;
  padding: 3px 10px;
}

.result-info {
  margin-top: 12px;
  font-size: 13px;
  color: var(--text-secondary);
}

.year-sep {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 18px 2px 10px;
  color: var(--text-secondary);
}

.year-sep b {
  font-size: 15px;
  flex-shrink: 0;
}

.year-sep .line {
  flex: 1;
  height: 1px;
  background: var(--border);
}

.work-row {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.03);
  padding: 10px;
  margin-top: 10px;
}

.work-row-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.work-poster {
  width: 64px;
  aspect-ratio: 2 / 3;
  object-fit: cover;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.25);
  flex-shrink: 0;
}

.work-poster-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
}

.work-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.work-title-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.work-title {
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.work-year {
  color: var(--text-secondary);
  font-size: 13px;
  flex-shrink: 0;
}

.tmdb-chip {
  flex-shrink: 0;
}

.work-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.work-path {
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.75;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.work-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
}

.work-files {
  margin-top: 10px;
  border-top: 1px dashed var(--border);
  padding-top: 10px;
}

.work-tmdb-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.work-overview {
  flex: 1 1 240px;
  min-width: 0;
  font-size: 12px;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.share-item-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.share-item-row .v-checkbox {
  flex: 1;
  min-width: 0;
}

.share-item-meta {
  color: var(--text-secondary);
  font-size: 12px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.extract-progress {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.extract-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.extract-nums {
  font-size: 12px;
  color: var(--text-secondary);
}

.transfer-log {
  margin-top: 8px;
  max-height: 160px;
  overflow: auto;
  font-size: 12px;
  color: var(--text-secondary);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.dir-picker-title {
  font-weight: 650;
  display: flex;
  align-items: center;
}

.dir-picker-breadcrumb {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  margin-bottom: 6px;
}

.dir-picker-list {
  max-height: 340px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
}

.lib-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.lib-meta {
  color: var(--text-secondary);
  font-size: 12px;
}

.hub-status-line {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}

.hub-offline-list {
  display: flex;
  flex-direction: column;
}

.hub-offline-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border);
}

.hub-offline-row:last-child {
  border-bottom: none;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 14px;
}

.pager-info {
  font-size: 13px;
  color: var(--text-secondary);
}

.storage-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.storage-ring {
  width: 180px;
  height: 180px;
  transform: rotate(-90deg);
}

.storage-ring-total {
  transform: rotate(90deg);
  transform-origin: 21px 21px;
  font-size: 4.2px;
  font-weight: 650;
  text-anchor: middle;
  fill: var(--text);
}

.storage-ring-sub {
  transform: rotate(90deg);
  transform-origin: 21px 21px;
  font-size: 3.2px;
  text-anchor: middle;
  fill: var(--muted);
}

.storage-legend {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.storage-legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.storage-dot {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  flex-shrink: 0;
}

.storage-name {
  min-width: 80px;
}

.storage-size {
  color: var(--text-secondary);
  font-size: 12px;
}
</style>
