import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { pdfFontAssets } from "./tooling/pdfAssets";
import { communityBoundary } from "./tooling/communityBoundary";
import { communityAssets } from "./tooling/communityAssets";
import { communityNotices } from "./tooling/communityNotices";

const root = dirname(fileURLToPath(import.meta.url));
export default defineConfig(({ command }) => {
  if (command !== "build") throw new Error("Use the Community production host; this config does not proxy the Enterprise dev API.");
  return {
    root, base: "/static/web/", plugins: [react(), pdfFontAssets(), communityNotices(), communityBoundary(), communityAssets()],
    build: {
      outDir: resolve(root, "dist/community"), emptyOutDir: true, sourcemap: false,
      rollupOptions: { input: resolve(root, "personal.html") },
    },
  };
});
