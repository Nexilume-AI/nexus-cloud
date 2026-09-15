import { afterEach, describe, expect, it, vi } from "vitest";
import { getLocale, setLocale, subscribeLocale } from "./locale";
import { defaultLocale, localeStorageKey } from "./model";
import { translate } from "./translate";
import { messages } from "./messages";
import { formatDate, formatMoney, formatNumber } from "../lib/format";

afterEach(() => vi.unstubAllGlobals());

describe("workspace localization", () => {
  it("defaults to Chinese and keeps an English fallback for unknown copy", () => {
    expect(defaultLocale).toBe("zh-CN");
    expect(translate("Providers", "zh-CN")).toBe("模型提供商");
    expect(translate("Providers", "en-US")).toBe("Providers");
    expect(translate("toString", "zh-CN")).toBe("toString");
    expect(translate("A new untranslated message", "zh-CN")).toBe("A new untranslated message");
  });

  it("preserves interpolated user content verbatim", () => {
    const name = '<script>agent("Providers")</script>';
    expect(translate("Open Run details · {{0}}", "zh-CN", { 0: name })).toContain(name);
    expect(translate("{{count}} resources", "zh-CN", { count: 12 })).toBe("共 12 项资源");
    expect(translate("{{missing}}", "zh-CN")).toBe("{{missing}}");
  });

  it("persists changes and only notifies subscribers when the language changes", () => {
    const storage = { setItem: vi.fn() };
    vi.stubGlobal("localStorage", storage);
    const listener = vi.fn();
    const unsubscribe = subscribeLocale(listener);
    setLocale("zh-CN"); setLocale("zh-CN");
    expect(getLocale()).toBe("zh-CN");
    expect(storage.setItem).toHaveBeenCalledWith(localeStorageKey, "zh-CN");
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe(); setLocale("en-US");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("works when browser storage is blocked", () => {
    vi.stubGlobal("localStorage", { setItem: () => { throw new Error("blocked"); } });
    expect(() => setLocale("zh-CN")).not.toThrow();
    expect(getLocale()).toBe("zh-CN");
  });

  it("formats dates, currencies and accounting units in the selected language", () => {
    setLocale("zh-CN");
    expect(formatDate(null)).toBe("从未");
    expect(formatDate("invalid")).toBe("invalid");
    expect(formatMoney(12.5, "USD")).toBe(new Intl.NumberFormat("zh-CN", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(12.5));
    expect(formatNumber(12345)).toBe(new Intl.NumberFormat("zh-CN").format(12345));
    expect(formatMoney(10, "token_credit")).toBe("10 token credit");
    setLocale("en-US");
    expect(formatDate(null)).toBe("Never");
    expect(formatMoney(12.5)).toBe("$12.50");
  });

  it("keeps placeholders intact throughout the Chinese catalog", () => {
    for (const [english, chinese] of Object.entries(messages)) {
      expect(chinese.trim(), english).not.toBe("");
      const placeholders = (text: string) => text.match(/\{\{\w+\}\}/g)?.sort() ?? [];
      expect(placeholders(chinese), english).toEqual(placeholders(english));
      expect(chinese, english).not.toMatch(/NEXUS(?:ID|PH)\d/);
    }
  });
});
