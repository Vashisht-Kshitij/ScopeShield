import { defineConfig, devices } from "@playwright/test";
const artifacts = process.env.SCOPESHIELD_E2E_ARTIFACTS || "../.local/day9";
export default defineConfig({
  testDir: "./e2e", timeout: 60_000, expect: { timeout: 12_000 }, fullyParallel: false, workers: 1, retries: 0,
  reporter: [["list"], ["json", { outputFile: `${artifacts}/playwright-results.json` }]],
  outputDir: `${artifacts}/playwright-output`,
  use: { ...devices["Desktop Chrome"], channel: "chrome", baseURL: "http://127.0.0.1:3000", viewport: { width: 1440, height: 1000 },
    trace: "off", video: "off", screenshot: "off" },
});
