# Localization browser verification

**English** · [Chinese](README_zh.md)

Install the test browser with `npx playwright install chromium`, then run `npm run test:i18n`.

Tests use a local Community production bundle and explicit mock API data to verify language switching, persistence after refresh, cross-tab synchronization, form-draft retention, Chinese navigation search and mobile layouts. Mock data is supplied only through test request interceptors; it is not included in the product build and does not prove the live backend is available.
