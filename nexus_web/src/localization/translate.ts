import { getLocale } from "./locale";
import type { Locale, MessageValues } from "./model";
import { messages } from "./messages";

/** English source messages are stable keys. Only presentation copy calls this. */
export function translate(message: string, locale: Locale, values?: MessageValues): string {
  const translated = locale === "zh-CN" && Object.hasOwn(messages, message) ? messages[message] : message;
  return translated.replace(/\{\{(\w+)\}\}/g, (placeholder, name: string) =>
    values && Object.hasOwn(values, name) ? String(values[name]) : placeholder);
}
export function t(message: string, values?: MessageValues): string {
  return translate(message, getLocale(), values);
}
