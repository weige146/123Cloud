import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  {
    path: "",
    redirect: "/admin/console",
  },
  {
    path: "/admin/",
    redirect: "/admin/console",
  },
  {
    path: "/admin/console",
    name: "console",
    component: () => import("@/views/ConsoleView.vue"),
    meta: { title: "控制台", desc: "服务状态与后端实时日志。", icon: "mdi-console" },
  },
  {
    path: "/admin/home",
    redirect: "/admin/console",
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
    path: "/admin/submission-routing",
    // 投稿路由已并入「投稿机器人」页的「频道路由」标签
    redirect: { path: "/admin/submission", query: { tab: "routing" } },
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
  { path: "/admin/:pathMatch(.*)*", redirect: "/admin/console" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0, behavior: "smooth" };
  },
});
