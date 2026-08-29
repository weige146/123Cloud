<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

const props = withDefaults(defineProps<{ compact?: boolean }>(), { compact: false });

const router = useRouter();
const open = ref(false);
const query = ref("");
const input = ref<{ focus?: () => void } | null>(null);
const activeIndex = ref(0);

const pages = computed(() => router.getRoutes()
  .filter((route) => route.meta?.title && route.meta?.icon)
  .map((route) => ({
    path: route.path,
    title: String(route.meta?.title || ""),
    desc: String(route.meta?.desc || ""),
    icon: String(route.meta?.icon || "mdi-circle-outline"),
  })));

const results = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase();
  if (!keyword) return pages.value.slice(0, 7);
  return pages.value
    .filter((page) => `${page.title} ${page.desc}`.toLocaleLowerCase().includes(keyword))
    .slice(0, 7);
});

async function showSearch() {
  open.value = true;
  activeIndex.value = 0;
  await nextTick();
  input.value?.focus?.();
}

function navigate(path: string) {
  open.value = false;
  query.value = "";
  router.push(path);
}

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    activeIndex.value = Math.min(activeIndex.value + 1, results.value.length - 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    activeIndex.value = Math.max(activeIndex.value - 1, 0);
  } else if (event.key === "Enter") {
    event.preventDefault();
    const target = results.value[activeIndex.value];
    if (target) navigate(target.path);
  } else if (event.key === "Escape") {
    event.preventDefault();
    open.value = false;
  }
}

function onKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  const typing = target?.matches("input, textarea, select, [contenteditable='true']");
  if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
    event.preventDefault();
    showSearch();
  } else if (!typing && event.key === "/") {
    event.preventDefault();
    showSearch();
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));
watch(results, () => { activeIndex.value = 0; });
</script>

<template>
  <v-menu v-model="open" :close-on-content-click="false" location="bottom center" :offset="10">
    <template #activator="{ props: activatorProps }">
      <v-btn
        v-if="compact"
        v-bind="activatorProps"
        icon="mdi-magnify"
        variant="text"
        class="shell-icon-button"
        aria-label="搜索页面"
        title="搜索页面"
      />
      <button v-else v-bind="activatorProps" type="button" class="nav-search-trigger" aria-label="搜索页面">
        <v-icon icon="mdi-magnify" size="20" />
        <span>搜索功能与设置…</span>
        <kbd>⌘ K</kbd>
      </button>
    </template>

    <v-card class="nav-search-panel" width="480" elevation="0">
      <div class="nav-search-input">
        <v-icon icon="mdi-magnify" size="21" />
        <input ref="input" v-model="query" type="search" placeholder="输入页面名称或功能" aria-label="搜索页面" @keydown="onSearchKeydown" />
        <button type="button" aria-label="关闭搜索" @click="open = false">
          <v-icon icon="mdi-close" size="18" />
        </button>
      </div>
      <div class="nav-search-results" role="listbox" aria-label="页面搜索结果">
        <button
          v-for="(page, index) in results"
          :key="page.path"
          type="button"
          class="nav-search-result"
          :class="{ active: activeIndex === index }"
          @mouseenter="activeIndex = index"
          @click="navigate(page.path)"
        >
          <span class="nav-search-icon"><v-icon :icon="page.icon" size="19" /></span>
          <span class="nav-search-copy">
            <strong>{{ page.title }}</strong>
            <small>{{ page.desc }}</small>
          </span>
          <v-icon icon="mdi-chevron-right" size="18" />
        </button>
        <div v-if="results.length === 0" class="nav-search-empty">
          <v-icon icon="mdi-database-search" size="28" />
          <span>没有找到相关页面</span>
        </div>
      </div>
      <footer class="nav-search-footer">
        <span><kbd>↑↓</kbd> 浏览</span>
        <span><kbd>Enter</kbd> 打开</span>
        <span><kbd>Esc</kbd> 关闭</span>
      </footer>
    </v-card>
  </v-menu>
</template>

<style scoped>
.nav-search-trigger {
  width: 100%;
  height: 38px;
  padding: 0 12px 0 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--surface-input);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 9px;
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color var(--transition), background-color var(--transition), box-shadow var(--transition);
}

.nav-search-trigger:hover {
  border-color: rgba(124, 92, 255, 0.45);
  background: var(--bg-hover);
  box-shadow: 0 0 0 3px rgba(124, 92, 255, 0.12);
}

.nav-search-trigger kbd { margin-left: auto; }

.nav-search-trigger span {
  flex: 1;
  min-width: 0;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

kbd {
  min-width: 28px;
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface-subtle);
  color: var(--text-muted);
  font: 500 11px/1.5 var(--font-sans);
  text-align: center;
  box-shadow: inset 0 -1px 0 var(--border);
}

.nav-search-panel {
  width: min(480px, calc(100dvw - 24px)) !important;
  overflow: hidden;
  border: 1px solid var(--overlay-border) !important;
  border-radius: var(--radius-dialog) !important;
  background: var(--overlay-surface) !important;
  background-image: var(--surface-sheen) !important;
  box-shadow: var(--shadow-overlay) !important;
  backdrop-filter: var(--overlay-filter);
  -webkit-backdrop-filter: var(--overlay-filter);
}

.nav-search-input {
  min-height: 58px;
  padding: 0 14px 0 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
}

.nav-search-input input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
  font: 500 15px/1.4 var(--font-sans);
}

.nav-search-input input::placeholder { color: var(--text-disabled); }

.nav-search-input button {
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}

.nav-search-results {
  max-height: min(460px, calc(100dvh - 190px));
  padding: 8px;
  overflow-y: auto;
}

.nav-search-result {
  width: 100%;
  min-height: 58px;
  padding: 8px 10px;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 11px;
  text-align: left;
  cursor: pointer;
}

.nav-search-result:hover,
.nav-search-result:focus-visible,
.nav-search-result.active {
  outline: 0;
  background: rgba(var(--v-theme-primary), var(--v-hover-opacity));
  color: rgb(var(--v-theme-primary));
}

.nav-search-icon {
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: 50%;
  background: rgba(var(--v-theme-primary), var(--v-selected-opacity));
  display: grid;
  place-items: center;
  color: rgb(var(--v-theme-primary));
}

.nav-search-copy {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-search-copy strong {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.nav-search-copy small {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-search-empty {
  min-height: 150px;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 13px;
}

.nav-search-footer {
  min-height: 38px;
  padding: 6px 14px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--text-muted);
  font-size: 10px;
}

.nav-search-footer span { display: inline-flex; align-items: center; gap: 5px; }
.nav-search-footer kbd { min-width: 23px; padding: 1px 4px; font-size: 9px; }

@media (max-width: 520px) {
  .nav-search-footer { display: none; }
  .nav-search-results { max-height: min(420px, calc(100dvh - 120px)); }
}
</style>
