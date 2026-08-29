import { createRouter, createWebHistory, type RouteRecordRaw, type NavigationGuardNext, type RouteLocationNormalized } from "vue-router";

const routes: RouteRecordRaw[] = [
  {
    path: "",
    redirect: "/admin/home",
  },
  {
    path: "/admin/login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
    meta: { public: true },
  },
  {
    path: "/admin/channel-settings",
    name: "channel-settings",
    component: () => import("@/views/ChannelSettingsView.vue"),
    meta: { public: true },
  },
  {
    path: "/admin/",
    redirect: "/admin/home",
  },
  {
    path: "/admin/home",
    name: "home",
    component: () => import("@/views/HomeView.vue"),
    meta: { title: "首页", desc: "服务状态与快捷入口。", icon: "mdi-view-dashboard-outline" },
  },
  {
    path: "/admin/submission",
    name: "submission",
    component: () => import("@/views/SubmissionView.vue"),
    meta: { title: "投稿机器人", desc: "Telegram Bot、TMDB 与投稿草稿。", icon: "mdi-robot" },
  },
  {
    path: "/admin/display",
    name: "display",
    component: () => import("@/views/DisplayView.vue"),
    meta: { title: "投稿展示", desc: "投稿模板、片源备注和分享按钮展示。", icon: "mdi-file-document-outline" },
  },
  {
    path: "/admin/transfer",
    name: "transfer",
    component: () => import("@/views/Pan115HubView.vue"),
    props: { initialTab: "transfer" },
    meta: { title: "115 搬运", desc: "115 分享搬运到 123 云盘，支持 Cookie 池、秒传和离线兜底。", icon: "mdi-cloud-sync" },
  },
  {
    path: "/admin/pan115-helper",
    name: "pan115-helper",
    component: () => import("@/views/Pan115HubView.vue"),
    props: { initialTab: "helper" },
    meta: { title: "115 助手", desc: "单账号提交离线磁力 / ed2k，并定时清理回收站。", icon: "mdi-tools" },
  },
  {
    path: "/admin/pan115-cookie",
    name: "pan115-cookie",
    component: () => import("@/views/Pan115HubView.vue"),
    props: { initialTab: "cookie" },
    meta: { title: "115 Cookie", desc: "扫码获取 115 Cookie。", icon: "mdi-cookie" },
  },
  {
    path: "/admin/settings",
    name: "settings",
    component: () => import("@/views/SettingsView.vue"),
    meta: { title: "设置", desc: "外观、数据目录与应用信息。", icon: "mdi-cog-outline" },
  },
  { path: "/admin/:pathMatch(.*)*", redirect: "/admin/home" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0, behavior: "smooth" };
  },
});

const LOGIN_PATH = "/admin/login";

function isAuthenticated(): boolean {
  const session = localStorage.getItem("admin_session");
  return !!session;
}

router.beforeEach((to: RouteLocationNormalized, _from: RouteLocationNormalized, next: NavigationGuardNext) => {
  const isPublic = to.meta?.public === true;

  if (!isPublic && !isAuthenticated()) {
    next({ path: LOGIN_PATH, query: { redirect: to.fullPath } });
    return;
  }

  if (to.path === LOGIN_PATH && isAuthenticated()) {
    const redirect = to.query.redirect as string || "/admin/home";
    next({ path: redirect });
    return;
  }

  next();
});
