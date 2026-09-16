import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: "html",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3107",
    trace: "on-first-retry",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : [
        {
          command: ".venv/bin/uvicorn api.index:app --port 8107",
          url: "http://127.0.0.1:8107/api/health",
          reuseExistingServer: false,
        },
        {
          command: "PYTHON_API_ORIGIN=http://127.0.0.1:8107 npm run dev -- --port 3107",
          url: "http://127.0.0.1:3107",
          reuseExistingServer: false,
        },
      ],
});
