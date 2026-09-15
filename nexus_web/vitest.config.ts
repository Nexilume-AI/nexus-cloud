import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  test: {
    include: ["src/**/*.test.{ts,tsx}", "tooling/**/*.test.{ts,tsx}"],
    setupFiles: ["./tooling/testLocale.ts"],
    maxWorkers: 2,
  },
});
