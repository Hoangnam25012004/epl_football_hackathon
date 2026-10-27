import { defineConfig, devices } from "@playwright/test";

// M10, the bug bash on the public Space (docs/day7-design.md sections 3.11-3.12): the same
// judge's path S1-S9 against the deployed app, no local server. Run it by hand:
//   npx playwright test -c playwright.space.config.ts
// SPACE_URL overrides the demo address. The Space allows 3 new private sessions per address
// in 10 minutes: each project makes one, so the three run one after another.
const SPACE_URL = process.env.SPACE_URL ?? "https://hnam2501-pundit-copilot.hf.space";

export default defineConfig({
  testDir: "tests/e2e-space",
  timeout: 600_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: SPACE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "edge-1366",
      grep: /@edge/,
      use: {
        ...devices["Desktop Edge"],
        channel: "msedge",
        viewport: { width: 1366, height: 768 },
      },
    },
    {
      name: "chrome",
      grep: /@chrome/,
      use: {
        ...devices["Desktop Chrome"],
        channel: "chrome",
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "phone",
      grep: /@phone/,
      use: { ...devices["Pixel 7"], channel: "msedge" },
    },
  ],
});
