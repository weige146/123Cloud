<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useGlobalState } from "@/composables/useGlobalState";
import { adminApi } from "@/api";

const router = useRouter();
const route = useRoute();
const { notifyError, notifySuccess, loadStatus } = useGlobalState();

const form = reactive({
  user: "",
  password: "",
  remember: true,
});

const loading = ref(false);
const showPassword = ref(false);

async function handleLogin() {
  if (!form.user.trim() || !form.password) {
    notifyError("请输入账号和密码");
    return;
  }

  loading.value = true;
  try {
    const result = await adminApi.login(form.user.trim(), form.password, form.remember);
    localStorage.setItem("admin_session", JSON.stringify(result));
    notifySuccess("登录成功");
    await loadStatus();
    router.push(String(route.query.redirect || "/admin/home"));
  } catch (error) {
    notifyError(error instanceof Error ? error.message : "登录失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login-page" data-group="dashboard">
    <div class="login-ambient" aria-hidden="true">
      <div class="login-orb login-orb--blue" />
      <div class="login-orb login-orb--violet" />
      <div class="login-orb login-orb--mint" />
      <div class="login-grid" />
    </div>

    <section class="login-card" aria-labelledby="login-title">
      <header class="login-header">
        <div class="login-logo" aria-hidden="true">123</div>
        <div class="login-brand">123Cloud</div>
        <h1 id="login-title">欢迎回来</h1>
        <p>登录以进入云盘工作台</p>
      </header>

      <form class="login-form" @submit.prevent="handleLogin">
        <label class="login-field">
          <span>账号</span>
          <v-text-field
            v-model="form.user"
            aria-label="账号"
            placeholder="请输入管理员账号"
            prepend-inner-icon="mdi-account-outline"
            variant="outlined"
            density="comfortable"
            hide-details
            autofocus
            autocomplete="username"
          />
        </label>

        <label class="login-field">
          <span>密码</span>
          <v-text-field
            v-model="form.password"
            aria-label="密码"
            :type="showPassword ? 'text' : 'password'"
            placeholder="请输入密码"
            prepend-inner-icon="mdi-lock-outline"
            variant="outlined"
            density="comfortable"
            hide-details
            autocomplete="current-password"
            :append-inner-icon="showPassword ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
            @click:append-inner="showPassword = !showPassword"
          />
        </label>

        <div class="login-options">
          <v-checkbox
            v-model="form.remember"
            label="保持登录"
            hide-details
            density="compact"
            color="primary"
          />
          <span class="login-security">
            <v-icon size="14">mdi-shield-lock-outline</v-icon>
            本机安全会话
          </span>
        </div>

        <v-btn
          color="primary"
          class="login-submit"
          :loading="loading"
          type="submit"
          block
          size="large"
        >
          <v-icon start>mdi-login</v-icon>
          登录
        </v-btn>
      </form>

      <footer class="login-footer">
        <span>123Cloud 管理后台</span>
        <span aria-hidden="true">·</span>
        <span>安全 · 可靠 · 高效</span>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  position: relative;
  min-height: 100dvh;
  padding: max(24px, env(safe-area-inset-top, 0px)) 16px max(24px, env(safe-area-inset-bottom, 0px));
  display: grid;
  place-items: center;
  overflow: hidden;
  background: var(--mesh-bg);
}

.login-ambient,
.login-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.login-orb {
  position: absolute;
  width: min(52vw, 620px);
  aspect-ratio: 1;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.34;
}

.login-orb--blue {
  top: -26%;
  left: -12%;
  background: var(--mesh-glow-1);
}

.login-orb--violet {
  right: -14%;
  bottom: -30%;
  background: var(--mesh-glow-2);
}

.login-orb--mint {
  right: 18%;
  top: 4%;
  width: min(32vw, 380px);
  background: var(--mesh-glow-3);
  opacity: 0.22;
}

.login-grid {
  display: none;
}

.login-card {
  position: relative;
  z-index: 1;
  width: min(100%, 400px);
  padding: 36px;
  border: 1px solid var(--glass-border-2);
  border-radius: var(--radius-surface);
  background: var(--surface-card);
  color: var(--text-primary);
  box-shadow: var(--shadow-overlay);
  background-image: var(--surface-sheen);
  backdrop-filter: var(--overlay-filter);
  -webkit-backdrop-filter: var(--overlay-filter);
}

.login-card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--glass-highlight), transparent);
}

.login-header {
  text-align: center;
  margin-bottom: 28px;
}

.login-logo {
  width: 58px;
  height: 58px;
  margin: 0 auto 12px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: rgb(var(--v-theme-primary));
  color: #fff;
  font-family: var(--font-mono);
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.03em;
  box-shadow: 0 10px 28px var(--group-glow), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}

.login-brand {
  color: var(--text-primary);
  font-size: 21px;
  font-weight: 800;
  letter-spacing: 0.05em;
}

.login-header h1 {
  margin: 18px 0 5px;
  font-size: 20px;
}

.login-header p {
  font-size: 13px;
  color: var(--text-muted);
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 17px;
}

.login-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.login-field > span {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.login-field :deep(.v-field) {
  min-height: 56px;
  background: var(--surface-input) !important;
  border-radius: 12px !important;
}

.login-field :deep(.v-field__control) {
  border-radius: 12px !important;
}

.login-options {
  min-height: 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.login-options :deep(.v-label) {
  font-size: 12.5px;
}

.login-security {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-muted);
  font-size: 11px;
}

.login-submit {
  height: 48px;
  font-size: 15px;
  font-weight: 700;
  background: var(--group-gradient) !important;
  color: #fff !important;
  box-shadow: 0 8px 22px var(--group-glow) !important;
}

.login-footer {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 10.5px;
}

@media (max-width: 480px) {
  .login-page {
    align-items: start;
    padding-top: max(74px, calc(env(safe-area-inset-top, 0px) + 64px));
  }

  .login-card {
    padding: 28px 22px 24px;
  }

  .login-security {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .login-card,
  .login-logo,
  .login-submit {
    transition: none !important;
  }
}
</style>
