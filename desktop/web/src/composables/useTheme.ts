import { reactive, readonly, watch } from "vue";
import type { ThemeInstance } from "vuetify";

export type ThemePreference = "auto" | "light" | "dark";
export type ResolvedTheme = Exclude<ThemePreference, "auto">;

const LEGACY_THEME_STORAGE_KEY = "admin-theme-preference";
const LEGACY_UI_STORAGE_KEY = "admin-ui-preferences-v2";
const UI_STORAGE_KEY = "admin-ui-preferences-v3";
const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)";

const state = reactive<{ preference: ThemePreference; resolved: ResolvedTheme }>({
  preference: "auto",
  resolved: "dark",
});

let vuetifyTheme: ThemeInstance | null = null;
let systemThemeQuery: MediaQueryList | null = null;

function normalizePreference(value: unknown): ThemePreference | null {
  if (value === "auto" || value === "light" || value === "dark") return value;
  if (value === "glass" || value === "transparent" || value === "purple") return "auto";
  return null;
}

function readPreference(): ThemePreference {
  try {
    const current = JSON.parse(localStorage.getItem(UI_STORAGE_KEY) || "{}") as { theme?: unknown };
    const normalized = normalizePreference(current.theme);
    if (normalized) return normalized;

    const previous = JSON.parse(localStorage.getItem(LEGACY_UI_STORAGE_KEY) || "{}") as { theme?: unknown };
    const previousNormalized = normalizePreference(previous.theme);
    if (previousNormalized) return previousNormalized;

    return normalizePreference(localStorage.getItem(LEGACY_THEME_STORAGE_KEY)) || "auto";
  } catch {
    return "auto";
  }
}

function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference !== "auto") return preference;
  return window.matchMedia?.(SYSTEM_DARK_QUERY).matches ? "dark" : "light";
}

function applyTheme() {
  state.resolved = resolveTheme(state.preference);
  const html = document.documentElement;
  html.dataset.theme = state.resolved;
  html.dataset.themePreference = state.preference;
  html.classList.toggle("dark", state.resolved === "dark");
  html.style.colorScheme = state.resolved;
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute(
    "content",
    state.resolved === "dark" ? "#111315" : "#f3f4f5"
  );
  vuetifyTheme?.change(state.resolved);
}

function persistPreference() {
  try {
    localStorage.setItem(UI_STORAGE_KEY, JSON.stringify({ theme: state.preference }));
    localStorage.removeItem(LEGACY_THEME_STORAGE_KEY);
    localStorage.removeItem(LEGACY_UI_STORAGE_KEY);
  } catch {}
}

export function initTheme(theme: ThemeInstance) {
  vuetifyTheme = theme;
  state.preference = readPreference();
  systemThemeQuery = window.matchMedia?.(SYSTEM_DARK_QUERY) || null;
  systemThemeQuery?.addEventListener("change", () => {
    if (state.preference === "auto") applyTheme();
  });
  applyTheme();

  watch(() => state.preference, () => {
    applyTheme();
    persistPreference();
  });
}

export function setTheme(preference: ThemePreference) {
  state.preference = preference;
}

export function toggleTheme() {
  state.preference = state.resolved === "light" ? "dark" : "light";
}

export function useTheme() {
  return { theme: readonly(state), setTheme, toggleTheme };
}
