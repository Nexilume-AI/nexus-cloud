# Frontend build and verification tools

**English** · [Chinese](README_zh.md)

These tools check Community build boundaries, static assets and third-party notices. `testLocale.ts` pins existing interface assertions to English; localization tests explicitly switch to and verify Chinese. `npm run build:community` builds the production bundle, and `npm run test:unit` runs frontend unit tests.
