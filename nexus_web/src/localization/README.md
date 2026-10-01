# Interface localization

**English** · [Chinese](README_zh.md)

The default language is Simplified Chinese. Selecting English in the global language picker takes effect immediately. The preference is stored as `nexus.locale` and synchronized across same-origin browser tabs. When storage is disabled, language switching still works on the current page.

`model.ts` defines the locale and message models, `locale.ts` manages framework-independent language state, `translate.ts` provides pure translation functions, and `useLocale.ts` adapts subscriptions for React. Switching languages does not reload or remount the application, or clear forms, chat drafts or terminals.

English source strings are message keys; Chinese dictionaries are organized by feature in `messages/`. Use `t("English copy")` where text is displayed, and `t("{{count}} items", { count })` for interpolation. Components with translated text call `useLocale()`; memoized translations must depend on the current locale. Format dates and numbers through `lib/format.ts`.

Translate interface explanations only. Preserve API fields, enum values, routes, model names, user content, commands and code. Select options must have explicit, stable `value` attributes. Module-level navigation and option models retain English message keys and translate at render time. Add dictionary entries with new UI copy. Missing translations fall back to English; interpolated values are plain text, never executable HTML.

All dictionaries ship in the static frontend bundle; no external translation service is called at runtime. `npm run test:i18n` validates the production bundle. Unit tests also check dictionary placeholders and source-message coverage.
