import { createApp } from "vue";
import { createVuetify } from "vuetify";
import "vuetify/styles";

import App from "./App.vue";
import { router } from "./router";
import "./styles/liquid.css";
import VDialogCloseBtn from "./components/VDialogCloseBtn.vue";
import PageHero from "./components/PageHero.vue";
import StatTile from "./components/StatTile.vue";
import SegmentedTabs from "./components/SegmentedTabs.vue";
import GlassCard from "./components/GlassCard.vue";
import { initTheme } from "./composables/useTheme";
import { appIcons } from "./icons";

const themeNames = ["light", "dark"];
const initialTheme = themeNames.includes(document.documentElement.dataset.theme || "")
  ? document.documentElement.dataset.theme as string
  : "dark";

const vuetify = createVuetify({
  icons: appIcons,
  theme: {
    defaultTheme: initialTheme,
    themes: {
      light: {
        dark: false,
        colors: {
          background: "#eef0fa",
          surface: "#ffffff",
          surfaceVariant: "rgba(255,255,255,0.45)",
          surfaceHover: "rgba(255,255,255,0.66)",
          primary: "#6a48ff",
          primaryLight: "#8b70ff",
          primaryDark: "#5a35f5",
          secondary: "#5b6072",
          success: "#0ea571",
          warning: "#c07714",
          error: "#e11d48",
          info: "#2563eb",
          text: "#171a2b",
          textSecondary: "rgba(28,32,54,0.78)",
          textMuted: "rgba(28,32,54,0.58)",
          textDisabled: "rgba(28,32,54,0.38)",
          border: "rgba(28,32,54,0.1)",
        },
      },
      dark: {
        dark: true,
        colors: {
          background: "#05060d",
          surface: "rgba(16,18,33,0.52)",
          surfaceVariant: "rgba(255,255,255,0.04)",
          surfaceHover: "rgba(255,255,255,0.08)",
          primary: "#7c5cff",
          primaryLight: "#9d85ff",
          primaryDark: "#5a35f5",
          secondary: "#a9aecb",
          success: "#34d399",
          warning: "#fbbf24",
          error: "#fb7185",
          info: "#60a5fa",
          text: "#eef1fc",
          textSecondary: "rgba(226,231,248,0.74)",
          textMuted: "rgba(219,225,245,0.55)",
          textDisabled: "rgba(219,225,245,0.36)",
          border: "rgba(255,255,255,0.1)",
        },
      },
    },
  },
});

const app = createApp(App);

app.use(vuetify);
app.use(router);
app.component("VDialogCloseBtn", VDialogCloseBtn);
app.component("PageHero", PageHero);
app.component("StatTile", StatTile);
app.component("SegmentedTabs", SegmentedTabs);
app.component("GlassCard", GlassCard);

initTheme(vuetify.theme);

app.mount("#app");
