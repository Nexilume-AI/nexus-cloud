import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.browser.ts",
  outputDir: "../../test-results/localization",
  use: { baseURL: "http://127.0.0.1:4178", viewport: { width: 1440, height: 1000 }, trace: "retain-on-failure" },
  webServer: { command: "node tooling/localization/preview.mjs", cwd: "../..", url: "http://127.0.0.1:4178", reuseExistingServer: false },
});
