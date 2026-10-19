import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./production-e2e", workers: 1, retries: 0, timeout: 90_000,
  outputDir: `${process.env.SCOPESHIELD_E2E_ARTIFACTS}/playwright-output`,
  reporter: [["list"], ["json", { outputFile: `${process.env.SCOPESHIELD_E2E_ARTIFACTS}/playwright-results.json` }]],
  use: { ...devices["Desktop Chrome"], channel: "chrome", baseURL: "https://demo.example.test",
    ignoreHTTPSErrors: true, // Generated one-day test certificate, no OS trust-store changes.
    launchOptions: { args: ["--host-resolver-rules=MAP demo.example.test 127.0.0.1:8443", "--no-proxy-server"] },
    trace: "off", video: "off", screenshot: "off" },
});
