import { defineConfig, devices } from "@playwright/test";

// E2E (docs/day4-design.md section 12, F5/F6): `e2e` runs against the real backend (mock LLM)
// serving the static build; `mock` replays recorded WebSocket streams with no backend at all.
// Local runs use the installed Edge (PW_CHANNEL=chrome for Chrome); CI installs Chromium.
const PORT = Number(process.env.E2E_PORT ?? 8765);
const MOCK_PORT = Number(process.env.E2E_MOCK_PORT ?? 8766);
const channel = process.env.CI ? undefined : (process.env.PW_CHANNEL ?? "msedge");
const server =
  process.env.E2E_SERVER ?? `uv run --project ../backend pundit serve --ui out --port ${PORT}`;

export default defineConfig({
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1600, height: 950 },
    ...(channel ? { channel } : {}),
  },
  projects: [
    {
      name: "e2e",
      testDir: "tests/e2e",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `http://127.0.0.1:${PORT}`,
        ...(channel ? { channel } : {}),
        viewport: { width: 1600, height: 950 },
      },
    },
    {
      name: "mock",
      testDir: "tests/e2e-mock",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `http://127.0.0.1:${MOCK_PORT}`,
        ...(channel ? { channel } : {}),
        viewport: { width: 1400, height: 900 },
      },
    },
  ],
  webServer: [
    {
      command: server,
      url: `http://127.0.0.1:${PORT}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: `node scripts/serve-static.mjs out ${MOCK_PORT}`,
      url: `http://127.0.0.1:${MOCK_PORT}/`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
});
