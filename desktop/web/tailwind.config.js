/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: {
          DEFAULT: "#F5F7FA",
          dark: "#0a0a0a",
        },
        surface: {
          50: "#FFFFFF",
          100: "#F5F7FA",
          200: "#F0F2F5",
          300: "#E8EBF0",
          400: "#D1D5DB",
          500: "#9CA3AF",
          600: "#6B7280",
          700: "#4B5563",
          800: "#374151",
          900: "#1F2937",
          950: "#111827",
          DEFAULT: "#FFFFFF",
          variant: "#F0F2F5",
          hover: "#E8EBF0",
        },
        "surface-dark": {
          50: "#232323",
          100: "#1f1f1f",
          200: "#1a1a1a",
          300: "#171717",
          400: "#141414",
          500: "#111111",
          600: "#0e0e0e",
          700: "#0b0b0b",
          800: "#080808",
          900: "#050505",
          950: "#030303",
          DEFAULT: "#141414",
          variant: "#1a1a1a",
          hover: "#232323",
        },
        primary: {
          50: "#F5F3FF",
          100: "#EDE9FE",
          200: "#DDD6FE",
          300: "#C4B5FD",
          400: "#A78BFA",
          500: "#8B5CF6",
          600: "#7C3AED",
          700: "#6D28D9",
          800: "#5B21B6",
          900: "#4C1D95",
          950: "#2E1065",
        },
        success: {
          light: "#2E7D32",
          dark: "#10B981",
        },
        warning: {
          light: "#ED6C02",
          dark: "#F59E0B",
        },
        error: {
          light: "#D32F2F",
          dark: "#EF4444",
        },
        info: {
          light: "#0288D1",
          dark: "#3B82F6",
        },
        text: {
          DEFAULT: "#1F1F1F",
          secondary: "#5C5C5C",
          muted: "#999999",
          disabled: "#CCCCCC",
        },
        "text-dark": {
          DEFAULT: "#ffffff",
          secondary: "#a1a1aa",
          muted: "#71717a",
          disabled: "#52525b",
        },
        border: {
          light: "rgba(0, 0, 0, 0.08)",
          dark: "rgba(255, 255, 255, 0.08)",
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'sans-serif'],
        display: ['Sora', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        mono: ['JetBrains Mono', 'SF Mono', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        card: '0 2px 12px rgba(0, 0, 0, 0.06), 0 0 0 1px rgba(0, 0, 0, 0.04)',
        'card-dark': '0 2px 12px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.06)',
        glow: '0 0 0 1px rgba(139, 92, 246, 0.2), 0 0 20px rgba(139, 92, 246, 0.18)',
      },
      animation: {
        'pulse-dot': 'pulse-dot 2s ease-in-out infinite',
        'fade-rise': 'fade-rise 0.35s cubic-bezier(0.16, 1, 0.3, 1) both',
        shimmer: 'shimmer 1.5s ease-in-out infinite',
      },
      keyframes: {
        'pulse-dot': {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.5', transform: 'scale(0.9)' },
        },
        'fade-rise': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
    },
  },
  plugins: [],
}
