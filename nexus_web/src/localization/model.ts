export const locales = ["zh-CN", "en-US"] as const;
export type Locale = typeof locales[number];
export type Messages = Readonly<Record<string, string>>;
export type MessageValues = Readonly<Record<string, string | number>>;
export const defaultLocale: Locale = "zh-CN";
export const localeStorageKey = "nexus.locale";
export function isLocale(value: unknown): value is Locale {
  return locales.includes(value as Locale);
}
