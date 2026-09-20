import { getLocale } from "./locale";
import { messages } from "./messages";

/** Machine tokens become display copy without leaking raw enum names. */
function candidates(value: string) {
  const raw = String(value ?? "").trim();
  if (!raw) return [];
  const spaced = raw.replace(/[._-]+/g, " ").toLowerCase();
  const titled = spaced.replace(/\b\w/g, (letter) => letter.toUpperCase());
  return [...new Set([raw, spaced, titled])];
}

/**
 * Translate a stable identifier (status, kind, scope) using the shared catalog.
 * Falls back to a readable Title Case form when the catalog has no entry, so
 * unknown server tokens never render as raw `snake_case`.
 */
export function tToken(value: string | null | undefined): string {
  for (const candidate of candidates(String(value ?? ""))) {
    if (getLocale() === "zh-CN" && Object.hasOwn(messages, candidate)) return messages[candidate];
  }
  const normalized = String(value ?? "").trim();
  if (!normalized) return "";
  return normalized.replace(/[._-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
