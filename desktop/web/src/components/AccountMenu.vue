<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { adminApi } from "@/api";
import { useGlobalState } from "@/composables/useGlobalState";

withDefaults(defineProps<{
  name?: string;
  meta?: string;
  avatarUrl?: string;
  initials?: string;
  authenticated?: boolean;
  loading?: boolean;
}>(), {
  name: "123Cloud 用户",
  meta: "等待账号连接",
  avatarUrl: "",
  initials: "12",
  authenticated: false,
  loading: false,
});

const emit = defineEmits<{ refresh: [] }>();
const router = useRouter();
const { notifyError } = useGlobalState();
const open = ref(false);
const loggingOut = ref(false);

function navigate(path: string) {
  open.value = false;
  router.push(path);
}

async function logout() {
  loggingOut.value = true;
  try {
    await adminApi.logout();
    localStorage.removeItem("admin_session");
    open.value = false;
    await router.replace("/admin/login");
  } catch (error) {
    notifyError(`退出失败：${error instanceof Error ? error.message : String(error)}`);
  } finally {
    loggingOut.value = false;
  }
}
</script>

<template>
  <v-menu v-model="open" :close-on-content-click="false" location="top start" :offset="10">
    <template #activator="{ props: activatorProps }">
      <button v-bind="activatorProps" type="button" class="account-trigger" aria-label="打开账号菜单">
        <span class="account-trigger-avatar">
          <img v-if="avatarUrl" :src="avatarUrl" alt="" />
          <span v-else>{{ initials }}</span>
        </span>
        <span class="account-trigger-status" :data-online="authenticated" />
      </button>
    </template>

    <v-card class="account-panel" width="300" elevation="0">
      <header class="account-panel-head">
        <div class="account-panel-avatar">
          <img v-if="avatarUrl" :src="avatarUrl" alt="" />
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
        <button type="button" :disabled="loading" @click="emit('refresh'); open = false">
          <span class="account-item-icon"><v-icon icon="mdi-refresh" size="20" /></span>
          <span><strong>刷新状态</strong><small>同步最新服务与登录状态</small></span>
          <v-progress-circular v-if="loading" indeterminate size="17" width="2" />
          <v-icon v-else icon="mdi-chevron-right" size="18" />
        </button>
      </div>

      <footer class="account-panel-footer">
        <v-btn block color="error" variant="tonal" :loading="loggingOut" prepend-icon="mdi-logout" @click="logout">
          退出登录
        </v-btn>
      </footer>
    </v-card>
  </v-menu>
</template>
