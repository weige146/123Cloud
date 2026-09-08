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
  type LibraryFile,
  type LibraryLibInfo,
  type LibraryShareItem,
  type LibraryShareTask,
  type LibraryStatus,
  type LibraryTransferTask,
  type LibraryWork,
  type TmdbDetail,
} from "@/api";
import { formatBytes } from "@/utils/format";
import { useGlobalState } from "@/composables/useGlobalState";

const { notifySuccess, notifyError, confirm } = useGlobalState();

type LibraryTab = "browse" | "share" | "config";
const tabsList: Array<{ key: LibraryTab; label: string; icon: string }> = [
  { key: "browse", label: "搜索浏览", icon: "mdi-magnify" },
  { key: "share", label: "分享提取", icon: "mdi-cloud-download-outline" },
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
    await Promise.all([loadStatus(), loadCategories(), loadSources()]);
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
      await Promise.all([loadStatus(), loadCategories(), loadSources()]);
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
    await Promise.all([loadStatus(), loadCategories(), loadSources()]);
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
    await Promise.all([loadStatus(), loadCategories(), loadSources()]);
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
    await Promise.all([loadStatus(), loadCategories(), loadSources(), searchWorks()]);
  } finally {
    scanning.value = false;
  }
}

// ===== 影库多选筛选 =====
const libFilter = ref<string[]>([]);
watch(libFilter, () => {
  page.value = 1;
  void loadCategories();
  void searchWorks();
});

// ===== 分类与搜索 =====
const categories = ref<LibraryCategory[]>([]);
const activeCat = ref("");
const activeSub = ref("");
const keyword = ref("");
const searching = ref(false);
const works = ref<LibraryWork[]>([]);
const totalWorks = ref(0);
const page = ref(1);
const pageSize = ref(20);
const searchNotice = ref("");

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

// 合并导出分类
const mergeMode = ref(false);
const mergeSelected = ref<Set<string>>(new Set());
const mergeExporting = ref(false);

function toggleMergeMode() {
  mergeMode.value = !mergeMode.value;
  if (!mergeMode.value) mergeSelected.value = new Set();
}

function toggleMergeCat(name: string) {
  const next = new Set(mergeSelected.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  mergeSelected.value = next;
}

async function exportMergedCategories() {
  const cats = Array.from(mergeSelected.value);
  if (!cats.length) {
    notifyError("请先勾选要合并导出的分类");
    return;
  }
  mergeExporting.value = true;
  try {
    const data = await libraryApi.exportSave({ cats: cats.join("|"), token: apiToken.value });
    notifySuccess(`已导出：${data.file} · ${data.totalFilesCount.toLocaleString()} 个文件 · ${data.formattedTotalSize}`);
  } catch (error) {
    notifyError(`合并导出失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    mergeExporting.value = false;
  }
}

// 分类折叠
const showAllCats = ref(false);
const showAllSubs = ref(false);
const CAT_LIMIT = 12;
const visibleCats = computed(() => (showAllCats.value ? categories.value : categories.value.slice(0, CAT_LIMIT)));
const activeCatNode = computed(() => categories.value.find((c) => c.name === activeCat.value) || null);
const visibleSubs = computed(() => {
  const subs = activeCatNode.value?.subs || [];
  return showAllSubs.value ? subs : subs.slice(0, CAT_LIMIT);
});

const CAT_ICON: Record<string, string> = {
  电影: "🎬", 电视剧: "📺", 剧集: "📺", 动漫: "🎨", 综艺: "🎤", 纪录片: "🌍",
  演唱会: "🎵", 儿童节目: "🧒", 短剧: "📱", 原盘ISO: "💿", 有声书: "🎧", 广播剧: "📻",
};

function catIcon(name: string): string {
  return CAT_ICON[name] || "📂";
}

function selectCat(name: string) {
  if (mergeMode.value) {
    toggleMergeCat(name);
    return;
  }
  activeCat.value = activeCat.value === name ? "" : name;
  activeSub.value = "";
  showAllSubs.value = false;
  page.value = 1;
  void searchWorks();
}

function selectSub(name: string) {
  activeSub.value = activeSub.value === name ? "" : name;
  page.value = 1;
  void searchWorks();
}

async function loadCategories() {
  try {
    const data = await libraryApi.categories(libFilter.value.join(","));
    categories.value = data.categories || [];
  } catch (error) {
    searchNotice.value = error instanceof Error ? error.message : String(error);
  }
}

async function searchWorks(record = false) {
  searching.value = true;
  searchNotice.value = "";
  try {
    const data = await libraryApi.search({
      q: keyword.value.trim(),
      cat: activeCat.value,
      sub: activeSub.value,
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
        : activeCat.value
          ? "该分类下没有作品"
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

const resultInfo = computed(() => {
  if (!totalWorks.value) return "";
  const where = activeCat.value + (activeSub.value ? ` / ${activeSub.value}` : "");
  if (browseMode.value) {
    return `分类 “${where || "全部"}” 共 ${totalWorks.value.toLocaleString()} 个作品 · 按年份降序`;
  }
  const head = activeCat.value ? `片名 “${keyword.value.trim()}”（分类：${where}）` : `片名 “${keyword.value.trim()}”`;
  return `${head} 共找到 ${totalWorks.value.toLocaleString()} 个作品`;
});

const yearGroups = computed(() => {
  if (!browseMode.value) return [{ year: null as number | null, label: "", items: works.value }];
  const groups: Array<{ year: number | null; label: string; items: LibraryWork[] }> = [];
  for (const work of works.value) {
    const last = groups[groups.length - 1];
    if (last && last.year === work.year) last.items.push(work);
    else groups.push({ year: work.year, label: work.year ? String(work.year) : "未知年份", items: [work] });
  }
  return groups;
});

function goToPage(next: number) {
  if (next < 1 || next > totalPages.value) return;
  page.value = next;
  void searchWorks();
}

function runSearch() {
  page.value = 1;
  void searchWorks(true);
}

let searchDebounce: number | undefined;
watch(keyword, () => {
  window.clearTimeout(searchDebounce);
  searchDebounce = window.setTimeout(() => {
    page.value = 1;
    void searchWorks(true);
  }, 300);
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
  if (expandedDir.value === work.dir) {
    expandedDir.value = null;
    dirFiles.value = [];
    detailSelected.value = new Set();
    return;
  }
  expandedDir.value = work.dir;
  dirFiles.value = [];
  detailSelected.value = new Set();
  detailVersion.value = "__all__";
  detailInfo.value = null;
  dirFilesLoading.value = true;
  void fetchTmdbDetail(work).then((detail) => {
    if (expandedDir.value === work.dir) detailInfo.value = detail;
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

// ===== 分享提取入库 =====
const shareUrl = ref("");
const shareTitle = ref("");
const extractCat = ref("");
const extractSub = ref("");
watch(extractCat, () => {
  extractSub.value = "";
});
const shareLoading = ref(false);
const shareBrowseOpen = ref(false);
const shareItems = ref<LibraryShareItem[]>([]);
const sharePath = ref<Array<{ id: string; name: string }>>([]);
const shareSelected = ref<Set<string>>(new Set());
const shareItemMap = ref<Map<string, LibraryShareItem>>(new Map());
const shareFilters = ref<Set<string>>(new Set());
const shareResumeInfo = ref<{ totalFiles: number; updatedAt: string } | null>(null);
const fileTypes: Record<string, string[]> = {
  视频: ["mp4", "mkv", "avi", "mov", "wmv", "flv", "ts", "m4v", "rmvb", "rm", "webm", "m2ts", "vob", "mpg", "mpeg", "3gp", "f4v"],
  字幕: ["srt", "ass", "ssa", "sub", "sup", "idx", "smi", "srtx", "vtt"],
  音频: ["mp3", "flac", "wav", "aac", "ogg", "m4a", "wma", "ape", "alac", "opus", "mka"],
  图片: ["jpg", "jpeg", "png", "bmp", "gif", "webp", "ico", "tiff", "tif", "heic", "svg"],
  文档: ["txt", "pdf", "nfo", "doc", "docx", "xls", "xlsx", "epub", "mobi", "info"],
  压缩包: ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"],
};
const shareExtractTask = ref<LibraryShareTask | null>(null);
const shareBatchInput = ref("");
const shareBatchRunning = ref(false);
const shareBatchLines = ref<Array<{ url: string; status: string; info: string }>>([]);
const shareHistoryList = ref<LibraryShareTask[]>([]);
let shareTimer: number | undefined;

const shareSelectedCount = computed(() => shareSelected.value.size);

async function loadShareLevel(parentId: string) {
  const data = await libraryApi.shareBrowse(shareUrl.value.trim(), parentId, apiToken.value);
  shareItems.value = data.items || [];
}

async function browseShare() {
  if (!shareUrl.value.trim()) {
    notifyError("请粘贴 123 分享链接");
    return;
  }
  shareLoading.value = true;
  try {
    sharePath.value = [];
    shareSelected.value = new Set();
    shareItemMap.value = new Map();
    await loadShareLevel("0");
    shareBrowseOpen.value = true;
    try {
      const cp = await libraryApi.shareCheckpoint(shareUrl.value.trim(), [], apiToken.value);
      shareResumeInfo.value = cp.checkpoint?.hasCheckpoint
        ? { totalFiles: cp.checkpoint.totalFiles || 0, updatedAt: cp.checkpoint.updatedAt || "" }
        : null;
    } catch {
      shareResumeInfo.value = null;
    }
  } catch (error) {
    notifyError(`浏览分享失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    shareLoading.value = false;
  }
}

async function enterShareFolder(item: LibraryShareItem) {
  shareLoading.value = true;
  try {
    sharePath.value = [...sharePath.value, { id: item.id, name: item.name }];
    await loadShareLevel(item.id);
  } catch (error) {
    sharePath.value = sharePath.value.slice(0, -1);
    notifyError(`进入文件夹失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    shareLoading.value = false;
  }
}

async function shareJumpTo(index: number) {
  shareLoading.value = true;
  try {
    sharePath.value = sharePath.value.slice(0, index + 1);
    await loadShareLevel(sharePath.value[index].id);
  } catch (error) {
    notifyError(`跳转失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    shareLoading.value = false;
  }
}

function shareGoRoot() {
  sharePath.value = [];
  void loadShareLevel("0");
}

function toggleShareItem(item: LibraryShareItem) {
  const next = new Set(shareSelected.value);
  if (next.has(item.id)) next.delete(item.id);
  else next.add(item.id);
  shareSelected.value = next;
  const map = new Map(shareItemMap.value);
  if (next.has(item.id)) map.set(item.id, item);
  shareItemMap.value = map;
}

function toggleShareAllVisible() {
  const next = new Set(shareSelected.value);
  const allSelected = shareItems.value.every((item) => next.has(item.id));
  for (const item of shareItems.value) {
    if (allSelected) next.delete(item.id);
    else {
      next.add(item.id);
      const map = new Map(shareItemMap.value);
      map.set(item.id, item);
      shareItemMap.value = map;
    }
  }
  shareSelected.value = next;
}

function toggleFilter(name: string) {
  const next = new Set(shareFilters.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  shareFilters.value = next;
}

async function startExtract(resume = false) {
  const selected = resume
    ? []
    : Array.from(shareSelected.value)
        .map((id) => shareItemMap.value.get(id))
        .filter((item): item is LibraryShareItem => Boolean(item));
  shareLoading.value = true;
  try {
    const data = await libraryApi.shareExtract({
      url: shareUrl.value.trim(),
      title: shareTitle.value.trim(),
      cat: extractCat.value,
      sub: extractSub.value,
      selectedItems: selected,
      fileFilters: Array.from(shareFilters.value),
      resume,
      token: apiToken.value,
    });
    shareBrowseOpen.value = false;
    shareResumeInfo.value = null;
    notifySuccess(resume ? "已从断点继续提取" : "提取任务已创建，扫完的秒传 JSON 会自动入库");
    await pollShareTask(data.taskId);
  } catch (error) {
    notifyError(`创建提取任务失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    shareLoading.value = false;
  }
}

async function extractWholeShare() {
  if (!shareUrl.value.trim()) {
    notifyError("请粘贴 123 分享链接");
    return;
  }
  await startExtract(false);
}

async function discardShareCheckpoint() {
  try {
    await libraryApi.deleteShareCheckpoint(shareUrl.value.trim(), [], apiToken.value);
    shareResumeInfo.value = null;
    notifySuccess("已放弃上次的提取进度");
  } catch (error) {
    notifyError(`操作失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function pollShareTask(taskId: string) {
  window.clearInterval(shareTimer);
  const tick = async () => {
    try {
      const data = await libraryApi.shareTask(taskId, apiToken.value);
      shareExtractTask.value = data.task;
      if (data.task.status !== "running") {
        window.clearInterval(shareTimer);
        await Promise.all([loadStatus(), loadCategories(), loadShareHistory()]);
      }
    } catch {
      window.clearInterval(shareTimer);
    }
  };
  await tick();
  shareTimer = window.setInterval(tick, 2000);
}

async function loadShareHistory() {
  try {
    shareHistoryList.value = (await libraryApi.shareHistory(apiToken.value)).tasks || [];
  } catch {
    /* 静默 */
  }
}

async function runBatchExtract() {
  const lines = shareBatchInput.value.split(/\n+/).map((l) => l.trim()).filter((l) => l);
  if (!lines.length) {
    notifyError("请先粘贴分享链接（每行一条，可带提取码）");
    return;
  }
  shareBatchRunning.value = true;
  shareBatchLines.value = lines.map((url) => ({ url, status: "等待", info: "" }));
  try {
    for (const line of shareBatchLines.value) {
      line.status = "提取中";
      try {
        const started = await libraryApi.shareExtract({
          url: line.url,
          cat: extractCat.value,
          sub: extractSub.value,
          fileFilters: Array.from(shareFilters.value),
          token: apiToken.value,
        });
        for (;;) {
          const t = await libraryApi.shareTask(started.taskId, apiToken.value);
          if (t.task.status !== "running") {
            if (t.task.status === "done") {
              line.status = "完成";
              line.info = `${t.task.result?.totalFilesCount ?? 0} 个文件`;
            } else {
              line.status = "失败";
              line.info = String(t.task.error || "");
            }
            break;
          }
          line.info = `已扫 ${t.task.progress.files} 个文件`;
          await new Promise((resolve) => setTimeout(resolve, 1500));
        }
      } catch (error) {
        line.status = "失败";
        line.info = error instanceof Error ? error.message : String(error);
      }
    }
    shareBatchInput.value = "";
    notifySuccess("批量提取完成，结果已入库");
    await Promise.all([loadStatus(), loadCategories(), loadShareHistory()]);
  } finally {
    shareBatchRunning.value = false;
  }
}

watch(tab, (value) => {
  if (value === "share") void loadShareHistory();
});

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
      activeCat: activeCat.value,
      activeSub: activeSub.value,
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
    if (["browse", "share", "config"].includes(state.tab)) tab.value = state.tab;
    activeCat.value = String(state.activeCat || "");
    activeSub.value = String(state.activeSub || "");
    page.value = Math.max(1, Number(state.page) || 1);
    keyword.value = String(state.keyword || "");
  } catch {
    /* 忽略 */
  }
}

watch([tab, activeCat, activeSub, page, keyword], saveUiState);

onMounted(async () => {
  restoreUiState();
  await Promise.all([loadConfig(), loadStatus(), loadCategories(), loadSources(), searchWorks()]);
  statusTimer = window.setInterval(loadStatus, 30000);
});

onUnmounted(() => {
  window.clearInterval(statusTimer);
  window.clearInterval(transferTimer);
  window.clearInterval(shareTimer);
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

      <GlassCard accent="group" icon="mdi-magnify" title="搜索影库" desc="片名模糊搜索；点分类按年份降序浏览；行内「展开文件」可勾选导出或转存。">
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

        <div class="cat-panel">
          <div class="cat-panel-head">
            <span class="cat-panel-title">📂 分类</span>
            <span class="muted-hint">点击分类按年份降序浏览 · 不输入片名也能用</span>
            <v-spacer />
            <v-btn
              size="small"
              :variant="mergeMode ? 'tonal' : 'text'"
              :color="mergeMode ? 'primary' : 'default'"
              prepend-icon="mdi-check"
              @click="toggleMergeMode"
            >合并导出</v-btn>
          </div>
          <div v-if="mergeMode" class="merge-toolbar">
            <span class="muted-hint">已选 {{ mergeSelected.size }} 个分类</span>
            <v-btn size="small" color="primary" prepend-icon="mdi-download" :loading="mergeExporting" :disabled="!mergeSelected.size" @click="exportMergedCategories">合并导出选中分类</v-btn>
            <v-btn size="small" variant="text" @click="mergeSelected = new Set()">清空选择</v-btn>
          </div>
          <div class="cat-bar">
            <template v-if="!mergeMode">
              <button type="button" class="cat-chip" :class="{ active: !activeCat }" @click="selectCat('')">全部</button>
              <button
                v-for="cat in visibleCats"
                :key="cat.name"
                type="button"
                class="cat-chip"
                :class="{ active: activeCat === cat.name }"
                @click="selectCat(cat.name)"
              >{{ catIcon(cat.name) }} {{ cat.name }}<small>{{ cat.count }}</small></button>
            </template>
            <template v-else>
              <button
                v-for="cat in visibleCats"
                :key="cat.name"
                type="button"
                class="cat-chip"
                :class="{ active: mergeSelected.has(cat.name) }"
                @click="toggleMergeCat(cat.name)"
              >{{ mergeSelected.has(cat.name) ? "☑" : "☐" }} {{ catIcon(cat.name) }} {{ cat.name }}<small>{{ cat.count }}</small></button>
            </template>
            <v-btn v-if="categories.length > CAT_LIMIT" size="x-small" variant="text" @click="showAllCats = !showAllCats">
              {{ showAllCats ? "收起" : `展开更多（${categories.length - CAT_LIMIT}）` }}
            </v-btn>
          </div>
          <div v-if="activeCat && !mergeMode" class="cat-bar sub-bar">
            <button
              v-for="sub in visibleSubs"
              :key="sub.name"
              type="button"
              class="cat-chip sub"
              :class="{ active: activeSub === sub.name }"
              @click="selectSub(sub.name)"
            >{{ sub.name }}<small>{{ sub.count }}</small></button>
            <v-btn v-if="(activeCatNode?.subs.length || 0) > CAT_LIMIT" size="x-small" variant="text" @click="showAllSubs = !showAllSubs">
              {{ showAllSubs ? "收起" : "展开更多" }}
            </v-btn>
          </div>
        </div>

        <div v-if="resultInfo" class="result-info">{{ resultInfo }}</div>
        <div v-if="searchNotice" class="hub-status-line">{{ searchNotice }}</div>

        <div v-if="searching" class="empty-state"><p>加载中…</p></div>
        <template v-else-if="works.length">
          <template v-for="group in yearGroups" :key="group.label + group.items.length">
            <div v-if="browseMode && yearGroups.length > 1" class="year-sep">
              <b>{{ group.label }}</b>
              <span class="line" />
            </div>
            <div v-for="work in group.items" :key="work.dir" class="work-row">
              <div class="work-row-head">
                <WorkPoster :work="work" :fetch-poster="fetchPoster" />
                <div class="work-main">
                  <div class="work-title-line">
                    <strong class="work-title" :title="work.dir">{{ work.title }}</strong>
                    <span v-if="work.year" class="work-year">({{ work.year }})</span>
                    <v-chip v-if="work.tmdbId" size="x-small" variant="tonal" class="tmdb-chip">TMDB:{{ work.tmdbId }}</v-chip>
                  </div>
                  <div class="work-meta">{{ work.videoCount }} 个视频 · {{ work.count }} 个文件 · {{ formatBytes(work.totalSize) }}</div>
                  <div class="work-path" :title="work.dir">{{ work.dir }}</div>
                </div>
                <div class="work-actions">
                  <v-btn size="small" variant="outlined" :color="expandedDir === work.dir ? 'primary' : 'default'" @click="toggleWorkFiles(work)">
                    {{ expandedDir === work.dir ? "收起文件" : "展开文件" }}
                  </v-btn>
                  <v-btn size="small" variant="outlined" prepend-icon="mdi-download" :loading="exporting === work.dir" @click="exportWork(work)">导出</v-btn>
                  <v-btn size="small" color="success" variant="tonal" prepend-icon="mdi-fast-forward" :loading="transferBusy === work.dir" :disabled="Boolean(transferBusy)" @click="submitTransferWork(work)">转存</v-btn>
                </div>
              </div>
              <div v-if="expandedDir === work.dir" class="work-files">
                <div v-if="detailInfo" class="work-tmdb-line">
                  <v-chip size="x-small" color="warning" variant="tonal">⭐ {{ detailInfo.voteAverage ? detailInfo.voteAverage.toFixed(1) : "—" }}</v-chip>
                  <span v-if="detailInfo.genres.length" class="muted-hint">{{ detailInfo.genres.join(" / ") }}</span>
                  <span class="work-overview" :title="detailInfo.overview">{{ detailInfo.overview || "暂无简介" }}</span>
                </div>
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
                    <v-btn size="small" variant="outlined" prepend-icon="mdi-download" :disabled="!detailSelected.size" @click="exportSelectedFiles(work)">导出选中</v-btn>
                    <v-btn size="small" color="success" variant="tonal" prepend-icon="mdi-fast-forward" :disabled="!detailSelected.size || Boolean(transferBusy)" @click="submitTransferSelectedFiles(work)">转存选中</v-btn>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </template>
        <div v-else-if="!searchNotice" class="empty-state"><p>输入片名，或点击上方分类浏览。</p></div>

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

    <!-- ====================== 分享提取 ====================== -->
    <div v-show="tab === 'share'" class="section-stack">
      <GlassCard accent="group" icon="mdi-cloud-download-outline" title="分享链接提取入库" desc="粘贴 123 分享链接直接提取整部分享入库；或「浏览分享」逐级勾选部分文件/文件夹。提取结果自动写入影库导入目录。">
        <FormGrid>
          <v-text-field
            v-model="shareUrl"
            label="123 分享链接（可带提取码）"
            placeholder="https://www.123pan.com/s/xxxx 提取码：xxxx"
            variant="outlined"
            density="compact"
            clearable
          />
          <v-text-field
            v-model="shareTitle"
            label="自定义作品名（可选）"
            placeholder="默认按分享内目录聚合"
            variant="outlined"
            density="compact"
            hide-details
          />
          <v-select
            v-model="extractCat"
            :items="categories.map((c) => c.name)"
            label="归入一级分类（可选）"
            variant="outlined"
            density="compact"
            clearable
            hide-details
          />
          <v-select
            v-model="extractSub"
            :items="(categories.find((c) => c.name === extractCat)?.subs || []).map((x) => x.name)"
            label="二级分类（可选）"
            variant="outlined"
            density="compact"
            clearable
            hide-details
            :disabled="!extractCat"
          />
        </FormGrid>

        <div class="button-row">
          <v-btn color="primary" prepend-icon="mdi-folder-search-outline" :loading="shareLoading" @click="browseShare">浏览分享…</v-btn>
          <v-btn color="success" variant="tonal" prepend-icon="mdi-cloud-download-outline" :loading="shareLoading" @click="extractWholeShare">整部分享直接提取</v-btn>
          <v-btn
            v-for="name in Object.keys(fileTypes)"
            :key="name"
            size="small"
            :variant="shareFilters.has(name) ? 'tonal' : 'outlined'"
            :color="shareFilters.has(name) ? 'primary' : 'default'"
            @click="toggleFilter(name)"
          >{{ name }}</v-btn>
          <span v-if="shareFilters.size" class="muted-hint">勾选的类型之外会被跳过；不勾=不过滤</span>
        </div>

        <div v-if="shareResumeInfo" class="extract-progress">
          <div class="extract-step">
            <v-icon icon="mdi-progress-clock" size="18" />
            检测到上次未完成的提取（已扫 {{ shareResumeInfo.totalFiles }} 个文件{{ shareResumeInfo.updatedAt ? ` · ${shareResumeInfo.updatedAt}` : "" }}）
          </div>
          <div class="button-row">
            <v-btn size="small" color="primary" @click="startExtract(true)">从断点继续提取</v-btn>
            <v-btn size="small" variant="text" @click="discardShareCheckpoint">放弃进度</v-btn>
          </div>
        </div>

        <div v-if="shareExtractTask" class="extract-progress">
          <div class="extract-step">
            <v-icon :icon="shareExtractTask.status === 'running' ? 'mdi-progress-clock' : shareExtractTask.status === 'done' ? 'mdi-check-circle' : 'mdi-alert-circle'" size="18" />
            {{ shareExtractTask.progress.step }}
          </div>
          <div v-if="shareExtractTask.status === 'running'" class="extract-nums">
            已扫 {{ shareExtractTask.progress.files }} 个文件 · {{ shareExtractTask.progress.dirs }} 个目录 · 跳过 {{ shareExtractTask.progress.skipped }}
          </div>
          <div v-else-if="shareExtractTask.result" class="extract-nums">
            {{ String(shareExtractTask.result.file || "") }} · {{ shareExtractTask.result.formattedTotalSize }}
          </div>
        </div>
      </GlassCard>

      <GlassCard icon="mdi-playlist-plus" title="批量链接提取" desc="每行一条分享链接（可带提取码），按顺序逐个提取入库，避免打爆分享接口。">
        <v-textarea
          v-model="shareBatchInput"
          :rows="4"
          label="分享链接列表"
          placeholder="https://www.123pan.com/s/aaaa 提取码：abcd&#10;https://www.123pan.com/s/bbbb"
          variant="outlined"
          density="compact"
        />
        <div class="button-row">
          <v-btn color="primary" prepend-icon="mdi-playlist-play" :loading="shareBatchRunning" @click="runBatchExtract">批量提取（{{ shareBatchLines.length || "" }}）</v-btn>
          <span v-if="shareFilters.size" class="muted-hint">应用上方当前的类型过滤与分类归属</span>
        </div>
        <div v-if="shareBatchLines.length" class="hub-offline-list">
          <div v-for="(line, index) in shareBatchLines" :key="index" class="hub-offline-row">
            <span class="lib-name">{{ line.url }}</span>
            <span class="share-item-meta">{{ line.status }} {{ line.info }}</span>
          </div>
        </div>
      </GlassCard>

      <GlassCard v-if="shareHistoryList.length" icon="mdi-history" title="提取历史" desc="最近 30 次提取任务（重启后清空）。">
        <div class="hub-offline-list">
          <div v-for="task in shareHistoryList" :key="task.taskId" class="hub-offline-row">
            <span class="lib-name">{{ String(task.result?.file || task.progress.step) }}</span>
            <span class="share-item-meta">
              {{ task.createdAt ? new Date(task.createdAt * 1000).toLocaleString() : "" }} ·
              {{ task.status === "done" ? `完成（${task.result?.formattedTotalSize || ""}）` : task.status === "running" ? "进行中" : "失败" }}
            </span>
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

      <GlassCard icon="mdi-key-outline" title="访问令牌" desc="开放局域网/外网访问时建议配置：影库搜索、文件、导出、分享提取与转存接口将要求携带令牌。油猴脚本里填同一串令牌即可。">
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

    <!-- ====================== 分享内容勾选弹窗 ====================== -->
    <v-dialog v-model="shareBrowseOpen" max-width="680">
      <v-card class="dir-picker-card">
        <v-card-title class="dir-picker-title">
          <v-icon icon="mdi-cloud-download-outline" size="22" class="mr-2" />
          勾选要提取的文件 / 文件夹（已选 {{ shareSelectedCount }}）
        </v-card-title>
        <v-card-text>
          <div class="dir-picker-breadcrumb">
            <v-btn size="small" variant="text" @click="shareGoRoot">根目录</v-btn>
            <template v-for="(item, index) in sharePath" :key="item.id">
              <v-icon icon="mdi-chevron-right" size="16" />
              <v-btn size="small" variant="text" @click="shareJumpTo(index)">{{ item.name }}</v-btn>
            </template>
          </div>
          <div v-if="shareLoading" class="empty-state"><p>目录加载中…</p></div>
          <div v-else-if="!shareItems.length" class="empty-state"><p>此目录为空。</p></div>
          <div v-else class="dir-picker-list">
            <div v-for="item in shareItems" :key="item.id" class="share-item-row">
              <v-checkbox
                :model-value="shareSelected.has(item.id)"
                density="compact"
                hide-details
                :label="item.name"
                @update:model-value="toggleShareItem(item)"
              />
              <span class="share-item-meta">
                <v-btn v-if="item.type === 1" size="x-small" variant="text" prepend-icon="mdi-folder-open-outline" @click="enterShareFolder(item)">进入</v-btn>
                <template v-else>{{ formatBytes(item.size) }}</template>
              </span>
            </div>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="toggleShareAllVisible">{{ shareItems.every((i) => shareSelected.has(i.id)) && shareItems.length ? "全不选" : "全选本层" }}</v-btn>
          <span class="muted-hint">勾选文件夹整体提取；可逐层进入累计勾选</span>
          <v-spacer />
          <v-btn variant="text" @click="shareBrowseOpen = false">取消</v-btn>
          <v-btn color="primary" :disabled="!shareSelectedCount" :loading="shareLoading" @click="startExtract(false)">提取选中（{{ shareSelectedCount }}）</v-btn>
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

.cat-panel {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
}

.cat-panel-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.cat-panel-title {
  font-size: 14px;
  font-weight: 700;
}

.merge-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
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
