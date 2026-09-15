import { defaultLocale, isLocale, localeStorageKey, type Locale } from "./model";

let locale = defaultLocale;
const listeners = new Set<() => void>();
try {
  const saved = globalThis.localStorage?.getItem(localeStorageKey);
  if (isLocale(saved)) locale = saved;
} catch { /* A blocked storage area must not prevent using the workspace. */ }

export function getLocale(): Locale { return locale; }
export function subscribeLocale(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function setLocale(next: Locale) {
  if (!isLocale(next)) return;
  try { globalThis.localStorage?.setItem(localeStorageKey, next); } catch { /* Session-only preference. */ }
  applyLocale(next);
}
function applyLocale(next: Locale) {
  if (typeof document !== "undefined") document.documentElement.lang = next;
  if (locale === next) return;
  locale = next;
  listeners.forEach(listener => listener());
}
if (typeof window !== "undefined") {
  applyLocale(locale);
  window.addEventListener("storage", event => {
    if (event.key === localeStorageKey || event.key === null) {
      applyLocale(isLocale(event.newValue) ? event.newValue : defaultLocale);
    }
  });
}
