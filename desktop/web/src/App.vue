<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useGlobalState } from "@/composables/useGlobalState";
import { useResponsive } from "@/composables/useResponsive";
import { displayName, formatBytes, normalizeAvatarUrl } from "@/utils/format";
import AccountMenu from "@/components/AccountMenu.vue";
import NavigationSearch from "@/components/NavigationSearch.vue";
import ThemeSettingsMenu from "@/components/ThemeSettingsMenu.vue";
import LiquidBackdrop from "@/components/LiquidBackdrop.vue";

interface NavigationItem {
  key: string;
  label: string;
  shortLabel: string;
  icon: string;
  path: string;
  routes: string[];
  children?: Array<{ label: string; path: string; icon: string }>;
}

const route = useRoute();
const router = useRouter();
const { state, loadStatus, setToast, confirmation, resolveConfirmation } = useGlobalState();
const { isMobile } = useResponsive();

const isDesktop = Boolean((window as unknown as { cloud123?: { isDesktop?: boolean } }).cloud123?.isDesktop);
const moreOpen = ref(false);

const navigation: NavigationItem[] = [
  { key: "home", label: "首页", shortLabel: "首页", icon: "mdi-view-dashboard-outline", path: "/admin/home", routes: ["/admin/home"] },
  {
    key: "submission",
    label: "投稿",
    shortLabel: "投稿",
    icon: "mdi-send",
    path: "/admin/submission",
    routes: ["/admin/submission", "/admin/display"],
    children: [
      { label: "投稿机器人", path: "/admin/submission", icon: "mdi-robot" },
      { label: "投稿展示", path: "/admin/display", icon: "mdi-file-document-outline" },
    ],
  },
  {
    key: "pan115",
    label: "115 中心",
    shortLabel: "115",
    icon: "mdi-cloud-sync",
    path: "/admin/transfer",
    routes: ["/admin/transfer", "/admin/pan115-helper", "/admin/pan115-cookie"],
    children: [
      { label: "115 搬运", path: "/admin/transfer", icon: "mdi-cloud-sync" },
      { label: "115 助手", path: "/admin/pan115-helper", icon: "mdi-tools" },
      { label: "115 Cookie", path: "/admin/pan115-cookie", icon: "mdi-cookie" },
    ],
  },
  { key: "settings", label: "设置", shortLabel: "设置", icon: "mdi-cog-outline", path: "/admin/settings", routes: ["/admin/settings"] },
];

const isPublicPage = computed(() => route.meta?.public === true);
const currentTitle = computed(() => String(route.meta?.title || "123Cloud"));
const pan = computed(() => state.status?.pan123);
const profile = computed(() => pan.value?.profile);
const panName = computed(() => displayName(profile.value || null, pan.value?.user || ""));
const panMeta = computed(() => {
  const currentProfile = profile.value;
  if (!currentProfile) return pan.value?.authenticated ? "账号已连接" : "未登录 123 云盘";
  const parts: string[] = [];
  if (currentProfile.vip) parts.push("VIP");
  const used = formatBytes(currentProfile.spaceUsed);
  const total = formatBytes(currentProfile.spacePermanent);
  if (used && total) parts.push(`${used} / ${total}`);
  else if (used || total) parts.push(used || `总容量 ${total}`);
  return parts.join(" · ") || "账号已连接";
});
const avatarUrl = computed(() => {
  const url = profile.value?.headImage?.trim();
  return url ? normalizeAvatarUrl(url) : "";
});
const avatarInitials = computed(() => panName.value.slice(0, 2) || "12");

const railGroups = computed(() => navigation.filter((item) => item.children));
const flatItems = computed(() => navigation.filter((item) => !item.children));

const mobileDock = computed(() => [navigation[0], navigation[1], navigation[2], navigation[3]]);

const morePages = computed(() => navigation.flatMap((item) => item.children || [{ label: item.label, path: item.path, icon: item.icon }]));

function isActive(item: NavigationItem) {
  return item.routes.includes(route.path);
}

function isChildActive(item: NavigationItem, child: { path: string }) {
  return item.routes.includes(child.path);
}

function navigate(path: string) {
  moreOpen.value = false;
  router.push(path);
}

onMounted(() => {
  if (!isPublicPage.value) loadStatus();
  const desktopApi = (window as unknown as {
    cloud123?: { onBackendStatus?: (callback: (payload: { state: string }) => void) => void };
  }).cloud123;
  desktopApi?.onBackendStatus?.((payload) => {
    if (payload.state === "crash") setToast("后端正在重启，请稍候…", "warn");
  });
});

watch(() => route.path, async () => {
  moreOpen.value = false;
  document.querySelector<HTMLElement>(".page-scroll")?.scrollTo({ top: 0 });
});

watch(isPublicPage, (isPublic) => {
  if (!isPublic && !state.loaded) loadStatus();
});
</script>

<template>
  <v-app class="app-shell">
    <LiquidBackdrop />

    <template v-if="!isPublicPage">
      <div v-if="isDesktop" class="drag-strip" />
      <div class="desktop-frame">
        <aside class="side-rail">
          <button type="button" class="side-rail__brand" aria-label="返回首页" @click="navigate('/admin/home')">
            <span class="brand-mark">123</span>
            <span class="brand-copy">
              <strong>123Cloud</strong>
              <small>Toolkit</small>
            </span>
          </button>

          <div class="side-rail__search">
            <NavigationSearch v-if="!isMobile" />
          </div>

          <nav class="side-rail__nav" aria-label="主导航">
            <template v-for="item in flatItems" :key="item.key">
              <button
                v-if="item.key !== 'settings'"
                type="button"
                class="nav-item"
                :class="{ active: isActive(item) }"
                @click="navigate(item.path)"
              >
                <v-icon :icon="item.icon" size="19" />
                <span>{{ item.label }}</span>
                <span v-if="item.key === 'home'" class="nav-dot" :data-online="Boolean(state.status)" />
              </button>
            </template>

            <template v-for="group in railGroups" :key="group.key">
              <span class="nav-group-label">{{ group.label }}</span>
              <button
                v-for="child in group.children"
                :key="child.path"
                type="button"
                class="nav-item"
                :class="{ active: isChildActive(group, child) }"
                @click="navigate(child.path)"
              >
                <v-icon :icon="child.icon" size="18" />
                <span>{{ child.label }}</span>
              </button>
            </template>

            <button
              type="button"
              class="nav-item"
              :class="{ active: isActive(navigation[3]) }"
              @click="navigate('/admin/settings')"
            >
              <v-icon icon="mdi-cog-outline" size="19" />
              <span>设置</span>
            </button>
          </nav>

          <footer class="side-rail__footer">
            <AccountMenu
              :name="panName"
              :meta="panMeta"
              :avatar-url="avatarUrl"
              :initials="avatarInitials"
              :authenticated="Boolean(pan?.authenticated)"
              :loading="state.loading"
              @refresh="loadStatus"
            />
            <span class="side-rail__account" :title="panMeta">
              <strong>{{ panName }}</strong>
              <small>{{ pan?.authenticated ? "已连接" : "未登录" }}</small>
            </span>
            <span class="side-rail__spacer" />
            <ThemeSettingsMenu button-class="rail-icon-button" />
            <v-btn
              v-if="!isDesktop"
              icon="mdi-refresh"
              variant="text"
              class="rail-icon-button"
              :loading="state.loading"
              aria-label="刷新状态"
              title="刷新状态"
              @click="loadStatus"
            />
          </footer>
        </aside>

        <main class="content-canvas">
          <div class="content-topline">
            <span class="content-title">{{ currentTitle }}</span>
            <span class="content-spacer" />
          </div>
          <div class="page-scroll">
            <div class="page-body">
              <router-view v-slot="{ Component }">
                <transition name="fade-rise" mode="out-in">
                  <component :is="Component" />
                </transition>
              </router-view>
            </div>
          </div>
        </main>
      </div>

      <nav v-if="isMobile" class="mobile-dock" aria-label="快捷导航">
        <button
          v-for="item in mobileDock"
          :key="item.key"
          type="button"
          class="mobile-dock-item"
          :class="{ active: isActive(item) }"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="navigate(item.path)"
        >
          <v-icon :icon="item.icon" size="20" />
          <span>{{ item.shortLabel }}</span>
        </button>
        <button type="button" class="mobile-dock-item" :class="{ active: moreOpen }" @click="moreOpen = true">
          <v-icon icon="mdi-dots-horizontal" size="20" />
          <span>更多</span>
        </button>
      </nav>

      <v-bottom-sheet v-if="moreOpen" v-model="moreOpen" class="mobile-more-sheet">
        <v-card class="mobile-more-card" elevation="0">
          <header class="mobile-more-head">
            <div>
              <strong>全部功能</strong>
              <span>切换页面</span>
            </div>
            <v-btn icon="mdi-close" variant="text" size="small" aria-label="关闭" @click="moreOpen = false" />
          </header>
          <div class="mobile-more-grid">
            <button
              v-for="page in morePages"
              :key="page.path"
              type="button"
              :class="{ active: route.path === page.path }"
              @click="navigate(page.path)"
            >
              <v-icon :icon="page.icon" size="21" />
              <span>{{ page.label }}</span>
            </button>
          </div>
        </v-card>
      </v-bottom-sheet>
    </template>

    <template v-else>
      <div class="public-theme-trigger"><ThemeSettingsMenu /></div>
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in"><component :is="Component" /></transition>
      </router-view>
    </template>

    <v-dialog
      v-if="confirmation.open"
      :model-value="confirmation.open"
      max-width="420"
      @update:model-value="(open) => { if (!open) resolveConfirmation(false) }"
    >
      <v-card class="confirm-dialog" elevation="0">
        <header class="confirm-dialog-head">
          <span class="confirm-dialog-icon"><v-icon icon="mdi-alert" size="22" /></span>
          <div>
            <strong>{{ confirmation.title }}</strong>
            <p>{{ confirmation.message }}</p>
          </div>
        </header>
        <footer class="confirm-dialog-actions">
          <v-btn variant="text" @click="resolveConfirmation(false)">取消</v-btn>
          <v-btn color="primary" @click="resolveConfirmation(true)">确认</v-btn>
        </footer>
      </v-card>
    </v-dialog>

    <v-snackbar
      v-if="state.toast"
      :color="state.toast.kind === 'success' ? 'success' : state.toast.kind === 'error' ? 'error' : state.toast.kind === 'warn' ? 'warning' : 'info'"
      timeout="4200"
      location="top right"
      class="app-toast"
    >
      <v-icon start size="18">
        {{ state.toast.kind === 'success' ? 'mdi-check-circle' : state.toast.kind === 'error' ? 'mdi-alert-circle' : state.toast.kind === 'warn' ? 'mdi-alert' : 'mdi-information' }}
      </v-icon>
      {{ state.toast.message }}
    </v-snackbar>
  </v-app>
</template>

<style scoped>
.side-rail__search {
  padding: 0 12px 8px;
}

.side-rail__footer {
  display: flex;
  align-items: center;
  gap: 9px;
}

.side-rail__account {
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}
.side-rail__account strong {
  font-size: 12.5px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-rail__account small { font-size: 10.5px; color: var(--text-muted); }
.side-rail__spacer { flex: 1; }

.rail-icon-button {
  color: var(--text-muted) !important;
}
.rail-icon-button:hover { color: var(--text-primary) !important; }

.content-topline {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 22px 0;
  -webkit-app-region: drag;
}
.content-topline .content-title {
  font-size: 12px;
  font-weight: 650;
  color: var(--text-muted);
  letter-spacing: 0.02em;
}
.content-topline .content-spacer { flex: 1; }

@media (max-width: 900px) {
  .content-topline { display: none; }
  .page-scroll { padding-top: 8px; }
}
</style>
