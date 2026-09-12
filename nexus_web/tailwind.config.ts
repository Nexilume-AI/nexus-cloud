import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#F2F1EA",
        panel: "#FBFCF8",
        subtle: "#E7E9E1",
        ink: "#071411",
        inkSoft: "#0D211B",
        muted: "#617069",
        line: "#CED3C9",
        brand: "#456B28",
        lume: "#BDFC73",
        bright: "#D9FFA0",
        accent: "#456B28",
        warn: "#b45309",
        danger: "#b91c1c",
        success: "#15803d"
      },
      fontFamily: {
        sans: ["Manrope", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI Variable", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "JetBrains Mono", "Cascadia Code", "Consolas", "monospace"]
      },
      boxShadow: {
        control: "0 1px 2px rgb(7 20 17 / 0.08)",
        panel: "0 1px 2px rgb(7 20 17 / 0.05), 0 12px 32px rgb(7 20 17 / 0.05)",
        overlay: "0 24px 64px rgb(7 20 17 / 0.24)",
        glow: "0 0 24px rgb(217 255 160 / 0.18)"
      }
    }
  },
  plugins: []
} satisfies Config;
