import { useEffect, useRef, useSyncExternalStore } from "react";
import { clearComposerDrafts } from "./composerDrafts";

// Tab-local drafts. Never persist tokens or attachment URLs/objects.
const STORAGE = "nexus.private-run-drafts.v1";
const MAX_TEXT = 32000;
const TTL = 24 * 60 * 60 * 1000;
export type FollowUpAttachments = { attachments?: { asset_id: string }[]; files?: string[] };
export type FollowUpSubmission = FollowUpAttachments & { mode: "queue" | "steer"; content: string; turn_index: number; idempotency_key: string };
// Persist only immutable IDs in an uncertain envelope. Never trim/drop a bad
// reference and then retry that changed envelope under the original key.
export function followUpAttachmentReferences(value: FollowUpAttachments): FollowUpAttachments {
  const id = (value: unknown) => typeof value === "string" && /^[\w-]{1,128}$/.test(value);
  if (value.attachments !== undefined && (!Array.isArray(value.attachments) || value.attachments.length > 4 || value.attachments.some(item => !item || !id(item.asset_id)))) throw new Error("Invalid image references");
  if (value.files !== undefined && (!Array.isArray(value.files) || value.files.length > 8 || value.files.some(item => !id(item)))) throw new Error("Invalid file references");
  return { ...(value.attachments?.length ? { attachments: value.attachments.map(item => ({ asset_id: item.asset_id })) } : {}), ...(value.files?.length ? { files: [...value.files] } : {}) };
}
type Entry = { text: string; updated: number; pending?: FollowUpSubmission };
type Ticket = { key: string; text: string };
let entries: Record<string, Entry> | undefined;
let persisted = true;
const listeners = new Set<() => void>();
function notify() { listeners.forEach(listener => listener()); }
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function load() {
  if (!entries) {
    entries = {};
    try {
      const raw: unknown = JSON.parse(sessionStorage.getItem(STORAGE) || "{}");
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        for (const [key, value] of Object.entries(raw).slice(0, 100)) {
          const item = value as Entry;
          if (key.startsWith("[") && item && typeof item.text === "string" && item.text.length <= MAX_TEXT && Number.isFinite(item.updated) && item.updated <= Date.now() && Date.now() - item.updated < TTL) {
            entries[key] = { text: item.text, updated: item.updated };
            const pending = item.pending;
            if (pending && ["queue", "steer"].includes(pending.mode) && typeof pending.content === "string" && pending.content.length <= MAX_TEXT && Number.isSafeInteger(pending.turn_index) && pending.turn_index > 0 && typeof pending.idempotency_key === "string" && pending.idempotency_key.length <= 128) {
              entries[key].pending = { mode: pending.mode, content: pending.content, turn_index: pending.turn_index, idempotency_key: pending.idempotency_key, ...followUpAttachmentReferences(pending) };
            }
          }
        }
      }
    } catch { persisted = false; }
  }
  return entries;
}
function save() {
  const current = load();
  const kept = Object.entries(current).filter(([, item]) => Date.now() - item.updated < TTL).sort((a, b) => b[1].updated - a[1].updated).slice(0, 100);
  entries = Object.fromEntries(kept);
  try {
    sessionStorage.setItem(STORAGE, JSON.stringify(Object.fromEntries(kept.filter(([, item]) => item.text.length <= MAX_TEXT))));
    persisted = kept.every(([, item]) => item.text.length <= MAX_TEXT);
  } catch { persisted = false; }
  notify();
}
export function runDraftKey(userId: string | number | undefined, organization: string | null | undefined, project: string | null | undefined, agent: string, run: string, slot = "chat") {
  return userId && organization && agent ? JSON.stringify([String(userId), organization, project || "", agent, run || "new", slot]) : "";
}
export function readRunDraft(key: string) {
  const entry = key ? load()[key] : undefined;
  return entry && Date.now() - entry.updated < TTL ? entry.text : "";
}
export function writeRunDraft(key: string, text: string) {
  if (!key) return;
  const current = load();
  if (text || current[key]?.pending) current[key] = { text, updated: Date.now(), pending: current[key]?.pending }; else delete current[key];
  save();
}
export function pendingFollowUp(key: string, mode: FollowUpSubmission["mode"], content: string) {
  const pending = readPendingFollowUp(key);
  return pending?.mode === mode && pending.content === content ? pending : undefined;
}
export function readPendingFollowUp(key: string) {
  const entry = load()[key];
  return entry && Date.now() - entry.updated < TTL ? entry.pending : undefined;
}
export function rememberFollowUp(key: string, pending: FollowUpSubmission) {
  if (!key) return;
  const current = load();
  current[key] = { text: current[key]?.text ?? pending.content, updated: Date.now(), pending: { mode: pending.mode, content: pending.content, turn_index: pending.turn_index, idempotency_key: pending.idempotency_key, ...followUpAttachmentReferences(pending) } };
  save();
}
export function settleFollowUp(key: string, idempotencyKey: string, clearMatchingDraft = true) {
  const entry = load()[key];
  if (!entry?.pending || entry.pending.idempotency_key !== idempotencyKey) return;
  const text = clearMatchingDraft && entry.text.trim() === entry.pending.content.trim() ? "" : entry.text;
  delete entry.pending;
  writeRunDraft(key, text);
}
export function clearSubmittedDraft(ticket: Ticket | undefined) {
  if (ticket && readRunDraft(ticket.key) === ticket.text) writeRunDraft(ticket.key, "");
}
export function clearRunDrafts() {
  clearComposerDrafts();
  entries = {};
  try { sessionStorage.removeItem(STORAGE); } catch { /* Nothing to clear when storage is unavailable. */ }
  notify();
}
export function useRunDraft(key: string) {
  const currentKey = useRef(key);
  currentKey.current = key;
  useEffect(() => {
    currentKey.current = key;
    return () => { currentKey.current = ""; };
  }, [key]);
  const text = useSyncExternalStore(subscribe, () => readRunDraft(key), () => "");
  const saved = useSyncExternalStore(subscribe, () => persisted, () => true);
  const pending = useSyncExternalStore(subscribe, () => readPendingFollowUp(key), () => undefined);
  return { text, setText: (value: string) => writeRunDraft(key, value), saved, pending,
    capture: (): Ticket => ({ key, text: readRunDraft(key) }),
    isCurrent: (ticket: Ticket | undefined) => ticket?.key === currentKey.current };
}

export function quotedReply(text: string, role: string, existing: string) {
  const excerpt = text.length > 2000 ? `${text.slice(0, 2000)}…` : text;
  return `> ${role === "user" ? "You" : "Agent"}\n${excerpt.split(/\r?\n/).map(line => `> ${line}`).join("\n")}\n\n${existing}`;
}
