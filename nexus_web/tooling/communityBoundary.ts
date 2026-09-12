import type { Plugin } from "vite";

export function isPrivateWebModule(id: string) {
  const path = id.replaceAll("\\", "/").split("?")[0];
  return /\/src\/(?:enterprise\/|main\.tsx$|pages\/tokenbank\/|components\/(?:tokenbank|marketplace)\/)/.test(path);
}
export function hasPrivateApiReference(source: string) {
  const decoded = source.replaceAll("\\/", "/");
  return /\/api\/v1\/(?:access(?:\/|\b)|service-accounts|role-bindings|access-grants|billing|tokenbank|marketplace|admin\/billing|dataset-acquisitions)/.test(decoded)
    || /dataset-acquisitions|marketplace\/datasets/.test(decoded);
}

export function hasPrivateStylesheetReference(source: string) {
  // CSS escapes must not hide a private selector in an otherwise neutral file.
  const normalized = source.replace(/\r\n?|\f/g, "\n");
  const decoded = normalized.replace(/\\(?:([0-9a-f]{1,6})[ \t\n]?|([^\n0-9a-f])|\n)/gi, (_match, hex: string | undefined, character: string | undefined) => {
    if (!hex) return character || "";
    const codepoint = Number.parseInt(hex, 16);
    return codepoint > 0 && codepoint <= 0x10ffff && !(codepoint >= 0xd800 && codepoint <= 0xdfff) ? String.fromCodePoint(codepoint) : "\ufffd";
  });
  return /(?:[.#]|--)(?:tokenbank|marketplace|provider-publish)(?:[-_]|\b)/i.test(decoded) || hasPrivateApiReference(decoded);
}

/** Build invariant, not a source-export/secret/license approval. */
export function communityBoundary(): Plugin {
  return {
    name: "community-distribution-boundary",
    generateBundle: { order: "post", handler(_options, bundle) {
      for (const id of this.getModuleIds()) {
        if (isPrivateWebModule(id)) this.error("COMMUNITY_PRIVATE_MODULE: " + id.replaceAll("\\", "/").split("/src/").at(-1));
      }
      for (const file of Object.values(bundle)) {
        if (file.type === "chunk" && hasPrivateApiReference(file.code)) {
          this.error("COMMUNITY_PRIVATE_API_REFERENCE: " + file.fileName);
        }
        if (file.type === "asset" && file.fileName.toLowerCase().endsWith(".css")) {
          const css = typeof file.source === "string" ? file.source : new TextDecoder().decode(file.source);
          if (hasPrivateStylesheetReference(css)) this.error("COMMUNITY_PRIVATE_STYLESHEET: " + file.fileName);
        }
      }
      const entry = bundle["personal.html"];
      if (!entry || entry.type !== "asset" || bundle["index.html"]) this.error("COMMUNITY_ENTRY_INVALID");
      delete bundle["personal.html"];
      entry.fileName = "index.html";
      bundle["index.html"] = entry;
    } },
  };
}
