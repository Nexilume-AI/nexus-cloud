import type { Plugin } from "vite";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";

// Keep font data same-origin in both development and deployed /static/web/ builds.
export function pdfFontAssets(): Plugin {
  const root = dirname(createRequire(import.meta.url).resolve("pdfjs-dist/package.json"));
  const assets = new Map<string, Buffer>();
  for (const directory of ["cmaps", "standard_fonts"]) {
    for (const name of readdirSync(join(root, directory))) {
      assets.set(`pdfjs/${directory}/${name}`, readFileSync(join(root, directory, name)));
    }
  }
  return {
    name: "local-pdf-font-assets",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const asset = assets.get((req.url ?? "").split("?")[0].replace(/^\//, ""));
        if (!asset) return next();
        res.setHeader("Content-Type", "application/octet-stream");
        res.setHeader("X-Content-Type-Options", "nosniff");
        res.end(asset);
      });
    },
    generateBundle() {
      for (const [fileName, source] of assets) this.emitFile({ type: "asset", fileName, source });
    },
  };
}
