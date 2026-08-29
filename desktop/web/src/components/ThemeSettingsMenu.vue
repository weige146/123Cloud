<script setup lang="ts">
import { computed, ref } from "vue";
import { useTheme, type ThemePreference } from "@/composables/useTheme";

withDefaults(defineProps<{ buttonClass?: string; label?: string }>(), {
  buttonClass: "",
  label: "外观设置",
});

const open = ref(false);
const { theme, setTheme } = useTheme();

const themeOptions: Array<{ value: ThemePreference; label: string; desc: string; icon: string }> = [
  { value: "auto", label: "跟随系统", desc: "透明玻璃自动切换明暗", icon: "mdi-theme-light-dark" },
  { value: "light", label: "浅色玻璃", desc: "明亮、中性的透明表面", icon: "mdi-white-balance-sunny" },
  { value: "dark", label: "深色玻璃", desc: "低眩光、中性的透明表面", icon: "mdi-weather-night" },
];

const triggerIcon = computed(() => {
  if (theme.preference === "auto") return "mdi-theme-light-dark";
  return themeOptions.find((item) => item.value === theme.preference)?.icon || "mdi-palette";
});

const currentLabel = computed(() => {
  return themeOptions.find((item) => item.value === theme.preference)?.label || "跟随系统";
});
</script>

<template>
  <v-menu v-model="open" :close-on-content-click="false" location="bottom end" :offset="8">
    <template #activator="{ props: activatorProps }">
      <v-btn
        v-bind="activatorProps"
        icon
        variant="text"
        :class="buttonClass"
        :aria-label="label"
        :title="label"
      >
        <v-icon size="20">{{ triggerIcon }}</v-icon>
      </v-btn>
    </template>

    <v-card class="theme-panel" width="480" elevation="0">
      <header class="theme-panel-head">
        <div>
          <strong>外观设置</strong>
          <span>{{ currentLabel }}</span>
        </div>
        <v-btn icon="mdi-close" size="small" variant="text" aria-label="关闭外观设置" @click="open = false" />
      </header>

      <section class="theme-panel-section" aria-labelledby="theme-mode-title">
        <h3 id="theme-mode-title">主题</h3>
        <div class="theme-option-grid">
          <button
            v-for="option in themeOptions"
            :key="option.value"
            type="button"
            class="theme-option"
            :class="[`theme-option--${option.value}`, { active: theme.preference === option.value }]"
            :aria-pressed="theme.preference === option.value"
            @click="setTheme(option.value)"
          >
            <span class="theme-preview" aria-hidden="true">
              <span class="theme-preview-sidebar" />
              <span class="theme-preview-card" />
            </span>
            <span class="theme-option-copy">
              <span class="theme-option-title"><v-icon :icon="option.icon" size="16" />{{ option.label }}</span>
              <span>{{ option.desc }}</span>
            </span>
            <v-icon v-if="theme.preference === option.value" icon="mdi-check-circle" size="18" class="theme-check" />
          </button>
        </div>
      </section>

    </v-card>
  </v-menu>
</template>

<style scoped>
.theme-panel {
  overflow: hidden;
  border: 1px solid var(--overlay-border) !important;
  border-radius: var(--radius-surface) !important;
  background: var(--overlay-surface) !important;
  background-image: var(--surface-sheen) !important;
  color: var(--text-primary) !important;
  box-shadow: var(--shadow-overlay) !important;
  backdrop-filter: var(--overlay-filter);
  -webkit-backdrop-filter: var(--overlay-filter);
}

.theme-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px 14px;
  border-bottom: 1px solid var(--border);
}

.theme-panel-head > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.theme-panel-head strong {
  font-size: 15px;
  font-weight: 600;
}

.theme-panel-head span {
  color: var(--text-muted);
  font-size: 12px;
}

.theme-panel-section {
  padding: 16px 18px 18px;
}

.theme-panel-section + .theme-panel-section {
  padding-top: 0;
}

.theme-panel-section h3 {
  margin: 0 0 10px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.theme-option-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.theme-option {
  position: relative;
  min-width: 0;
  min-height: 92px;
  padding: 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  transition: border-color var(--transition), background-color var(--transition), color var(--transition);
}

.theme-option:hover {
  border-color: rgba(var(--v-theme-primary), 0.5);
  background: var(--surface-hover);
}

.theme-option.active {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), var(--v-selected-opacity));
  color: rgb(var(--v-theme-primary));
}

.theme-preview {
  position: relative;
  width: 100%;
  height: 42px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 5px;
  background: #f4f5fa;
}

.theme-preview-sidebar {
  position: absolute;
  inset: 0 auto 0 0;
  width: 12px;
  background: #fff;
  border-right: 1px solid rgba(58, 53, 65, 0.12);
}

.theme-preview-card {
  position: absolute;
  top: 10px;
  right: 6px;
  width: 21px;
  height: 17px;
  border-radius: 3px;
  background: #fff;
  box-shadow: 0 3px 8px rgba(58, 53, 65, 0.14);
}

.theme-option--dark .theme-preview { background: #0b1220; }
.theme-option--dark .theme-preview-sidebar,
.theme-option--dark .theme-preview-card { background: #111a2c; }
.theme-option--auto .theme-preview { background: linear-gradient(135deg, #f5f7fb 0 50%, #0b1220 50%); }
.theme-option--auto .theme-preview-sidebar { background: rgba(37, 99, 235, 0.18); }
.theme-option--auto .theme-preview-card { background: rgba(255, 255, 255, 0.76); }

.theme-option-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.theme-option-title {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
}

.theme-option-copy > span:last-child {
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.3;
}

.theme-check {
  position: absolute;
  top: 6px;
  right: 6px;
  color: rgb(var(--v-theme-primary));
}

@media (max-width: 390px) {
  .theme-panel {
    width: min(360px, calc(100vw - 20px)) !important;
  }

  .theme-panel-head,
  .theme-panel-section {
    padding-inline: 14px;
  }

  .theme-option-grid {
    grid-template-columns: 1fr;
  }
}
</style>
