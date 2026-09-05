<script setup lang="ts">
import { ref, watch } from "vue";
import { useRouter } from "vue-router";

const props = withDefaults(defineProps<{
  name?: string;
  meta?: string;
  avatarUrl?: string;
  initials?: string;
  authenticated?: boolean;
  loading?: boolean;
  expired?: boolean;
}>(), {
  name: "123Cloud 用户",
  meta: "等待账号连接",
  avatarUrl: "",
  initials: "12",
  authenticated: false,
  loading: false,
  expired: false,
});

const emit = defineEmits<{ refresh: [] }>();
const router = useRouter();
const open = ref(false);

// 123 云盘 CDN 的头像链接可能过期失效（NoSuchKey），加载失败时回退到首字母头像
const avatarFailed = ref(false);
watch(
  () => props.avatarUrl,
  () => {
    avatarFailed.value = false;
  },
);

function navigate(path: string) {
  open.value = false;
  router.push(path);
}
</script>

<template>
  <v-menu v-model="open" :close-on-content-click="false" location="top start" :offset="10">
    <template #activator="{ props: activatorProps }">
      <button v-bind="activatorProps" type="button" class="account-trigger" aria-label="打开账号菜单">
        <span class="account-trigger-avatar">
          <img v-if="avatarUrl && !avatarFailed" :src="avatarUrl" alt="" @error="avatarFailed = true" />
          <span v-else>{{ initials }}</span>
        </span>
        <span class="account-trigger-status" :data-online="authenticated" />
      </button>
    </template>

    <v-card class="account-panel" width="300" elevation="0">
      <header class="account-panel-head">
        <div class="account-panel-avatar">
          <img v-if="avatarUrl && !avatarFailed" :src="avatarUrl" alt="" @error="avatarFailed = true" />
          <span v-else>{{ initials }}</span>
        </div>
        <div class="account-panel-copy">
          <span class="account-role">管理员</span>
          <strong>{{ name }}</strong>
          <small>{{ meta }}</small>
        </div>
      </header>

      <div class="account-panel-section">
        <button type="button" @click="navigate('/admin/submission')">
          <span class="account-item-icon"><v-icon icon="mdi-robot" size="20" /></span>
          <span><strong>投稿机器人</strong><small>Bot 配置与投稿草稿</small></span>
          <v-icon icon="mdi-chevron-right" size="18" />
        </button>
        <button type="button" @click="navigate('/admin/transfer')">
          <span class="account-item-icon"><v-icon icon="mdi-cloud-sync" size="20" /></span>
          <span><strong>115 搬运</strong><small>搬运配置与任务队列</small></span>
          <v-icon icon="mdi-chevron-right" size="18" />
        </button>
        <button type="button" @click="navigate('/admin/settings')">
          <span class="account-item-icon"><v-icon :icon="expired ? 'mdi-alert' : 'mdi-link-variant'" size="20" /></span>
          <span><strong>授权 123 网盘</strong><small>{{ expired ? "授权已失效，请重新授权" : authenticated ? "管理已授权账号" : "前往设置页授权登录" }}</small></span>
          <v-icon icon="mdi-chevron-right" size="18" />
        </button>
        <button type="button" :disabled="loading" @click="emit('refresh'); open = false">
          <span class="account-item-icon"><v-icon icon="mdi-refresh" size="20" /></span>
          <span><strong>刷新状态</strong><small>同步最新服务与授权状态</small></span>
          <v-progress-circular v-if="loading" indeterminate size="17" width="2" />
          <v-icon v-else icon="mdi-chevron-right" size="18" />
        </button>
      </div>
    </v-card>
  </v-menu>
</template>
