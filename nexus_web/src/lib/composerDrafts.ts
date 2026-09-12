import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import type { AgentFileTransfer } from "./api";

const STORAGE = "nexus.composer-assets.v1";
const TTL = 24 * 3600_000;
export type DraftAsset = { id: string; kind: "image" | "upload" | "computer" | "audio" | "run_file" | "run_image"; name: string; size: number; contentType: string; runId?: string };
export type ComposerDraft = { assets: DraftAsset[]; tool: string; profileId: string; reasoningEffort: string; updated: number };
const EMPTY: ComposerDraft = { assets: [], tool: "", profileId: "", reasoningEffort: "", updated: 0 };
let cache: Record<string, ComposerDraft> | undefined;
let saved = true;
const listeners = new Set<() => void>();
const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
const short = (value: unknown, max: number) => typeof value === "string" && value.length <= max ? value : "";
// Explicit projection: never serialize arbitrary API objects, File, blob, URLs,
// credentials, paths, or computer connection details.
export function sanitizeComposerDraft(value: unknown): ComposerDraft {
  const row = value && typeof value === "object" ? value as Partial<ComposerDraft> : {};
  const assets: DraftAsset[] = [];
  for (const item of Array.isArray(row.assets) ? row.assets.slice(0, 12) : []) {
    if (!item || !["image", "upload", "computer", "audio", "run_file", "run_image"].includes(item.kind) || typeof item.id !== "string" || !/^[\w-]{1,128}$/.test(item.id) || !Number.isSafeInteger(item.size) || item.size < 0) continue;
    if (item.kind.startsWith("run_") && (typeof item.runId !== "string" || !/^[\w-]{1,128}$/.test(item.runId))) continue;
    if (assets.some(asset => asset.id === item.id && asset.kind === item.kind)) continue;
    assets.push({ id: item.id, kind: item.kind, name: short(item.name, 255).split(/[\\/]/).pop() || "Attachment", size: item.size, contentType: short(item.contentType, 128), ...(item.kind.startsWith("run_") ? { runId: item.runId } : {}) });
  }
  return { assets, tool: short(row.tool, 256), profileId: short(row.profileId, 256), reasoningEffort: short(row.reasoningEffort, 64), updated: typeof row.updated === "number" ? row.updated : 0 };
}
function load() {
  if (!cache) {
    cache = {};
    try {
      const raw = JSON.parse(sessionStorage.getItem(STORAGE) || "{}");
      for (const [key, value] of Object.entries(raw || {}).slice(0, 100)) {
        const draft = sanitizeComposerDraft(value);
        if (key.startsWith("[") && draft.updated <= Date.now() && Date.now() - draft.updated < TTL) cache[key] = draft;
      }
    } catch { saved = false; }
  }
  return cache;
}
export function readComposerDraft(key: string) { const row = load()[key]; return row && Date.now() - row.updated < TTL ? row : EMPTY; }
export function writeComposerDraft(key: string, value: ComposerDraft) {
  if (!key) return;
  const rows = load();
  rows[key] = { ...sanitizeComposerDraft(value), updated: Date.now() };
  cache = Object.fromEntries(Object.entries(rows).filter(([, row]) => Date.now() - row.updated < TTL).sort((a, b) => b[1].updated - a[1].updated).slice(0, 100));
  try { sessionStorage.setItem(STORAGE, JSON.stringify(cache)); saved = true; } catch { saved = false; }
  listeners.forEach(fn => fn());
}
export function clearComposerDrafts() {
  cache = {};
  try { sessionStorage.removeItem(STORAGE); } catch { /* memory-only fallback */ }
  listeners.forEach(fn => fn());
}
export function clearSubmittedAssets(ticket?: { key: string; assets: DraftAsset[] }) {
  if (!ticket) return;
  const row = readComposerDraft(ticket.key);
  writeComposerDraft(ticket.key, { ...row, assets: row.assets.filter(asset => !ticket.assets.some(sent => sent.kind === asset.kind && sent.id === asset.id)) });
}
export function fileDraftAsset(file: AgentFileTransfer, kind: DraftAsset["kind"]): DraftAsset {
  return { id: file.file_id, kind, name: file.name, size: file.size_bytes, contentType: file.content_type };
}
export function useComposerDraft(key: string) {
  const [generation, setGeneration] = useState(0);
  const snapshot = useSyncExternalStore(subscribe, () => readComposerDraft(key), () => EMPTY);
  const persisted = useSyncExternalStore(subscribe, () => saved, () => true);
  // Existing uploads keep their own progress UI. Only records present on mount
  // or conversation return are restored, avoiding duplicate chips and uploads.
  const restored = useMemo(() => new Set(readComposerDraft(key).assets.map(asset => `${asset.kind}:${asset.id}`)), [key, generation]);
  const live = useMemo(() => new Map<string, Set<string>>(), [key, generation]);
  const replace = useCallback((kind: DraftAsset["kind"], assets: DraftAsset[]) => {
    const before = readComposerDraft(key);
    const previous = live.get(kind) || new Set();
    live.set(kind, new Set(assets.map(asset => asset.id)));
    const next = [...before.assets.filter(asset => asset.kind !== kind || !previous.has(asset.id)), ...assets];
    const normalized = sanitizeComposerDraft({ ...before, assets: next });
    if (JSON.stringify(before.assets) !== JSON.stringify(normalized.assets)) writeComposerDraft(key, normalized);
  }, [key, live]);
  const uploads = useCallback((files: AgentFileTransfer[]) => replace("upload", files.map(file => fileDraftAsset(file, "upload"))), [replace]);
  const computer = useCallback((files: AgentFileTransfer[]) => replace("computer", files.map(file => fileDraftAsset(file, "computer"))), [replace]);
  const audio = useCallback((files: AgentFileTransfer[]) => replace("audio", files.map(file => fileDraftAsset(file, "audio"))), [replace]);
  return { snapshot, persisted, restored: snapshot.assets.filter(asset => restored.has(`${asset.kind}:${asset.id}`)), uploads, computer, audio, replace,
    restore: () => setGeneration(value => value + 1),
    remove: (asset: DraftAsset) => writeComposerDraft(key, { ...readComposerDraft(key), assets: readComposerDraft(key).assets.filter(item => item.id !== asset.id || item.kind !== asset.kind) }),
    choose: (tool: string, choice: { profileId: string; reasoningEffort: string }) => writeComposerDraft(key, { ...readComposerDraft(key), tool, ...choice }),
    capture: () => ({ key, assets: readComposerDraft(key).assets }),
  };
}
