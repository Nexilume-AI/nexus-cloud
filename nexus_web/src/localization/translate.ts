import { getLocale } from "./locale";
import type { Locale, MessageValues } from "./model";
import { messages } from "./messages";
import type { Messages } from "./model";

const editionMessages: Record<string, string> = {};
/** Edition entry points register their own copy; personal bundles exclude it. */
export function registerMessages(catalog: Messages) {
  Object.assign(editionMessages, catalog);
}

/** English source messages are stable keys. Only presentation copy calls this. */
export function translate(message: string, locale: Locale, values?: MessageValues): string {
  const translated = locale !== "zh-CN" ? message
    : Object.hasOwn(editionMessages, message) ? editionMessages[message]
    : Object.hasOwn(messages, message) ? messages[message] : message;
  return translated.replace(/\{\{(\w+)\}\}/g, (placeholder, name: string) =>
    values && Object.hasOwn(values, name) ? String(values[name]) : placeholder);
}
export function t(message: string, values?: MessageValues): string {
  return translate(message, getLocale(), values);
}
