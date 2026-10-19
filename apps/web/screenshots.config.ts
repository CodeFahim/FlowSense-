import { defineConfig, devices } from "@playwright/test";

// Captures documentation screenshots (light and dark) from the running stack:
//   npx playwright test --config screenshots.config.ts
export default defineConfig({
  testDir: "./screenshots",
  timeout: 180_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: process.env.LIFELINE_WEB_URL ?? "http://localhost:3000", viewport: { width: 1360, height: 900 }, deviceScaleFactor: 1 },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1360, height: 900 } } }],
});
