import { defineConfig, devices } from "@playwright/test";

// E2E (docs/day4-design.md section 12, F5/F6): `e2e` runs against the real backend (mock LLM)
// serving the static build; `mock` replays recorded WebSocket streams with no backend at all;
// Day 6 `chaos` runs against a backend whose every LLM call fails (docs/day6-design.md 5.5).
// Local runs use the installed Edge (PW_CHANNEL=chrome for Chrome); CI installs Chromium.
// Day 5: the backend reads with the mock voice, ten times shorter than the real one
// (docs/day5-design.md section 12.6); a server started by hand needs the same variables.
export const E2E_SERVER_ENV = {
  TTS_PROVIDER: "mock",
  MOCK_TTS_TIME_SCALE: "0.1",
  MOCK_TTS_LATENCY_MS: "100",
  STT_PROVIDER: "mock",
  // Day 6: the suite makes several private sessions from 127.0.0.1, run after run
  PRIVATE_PER_IP: "100",
};
// Day 6: the mock LLM fails every role of every claim and studio job; short budgets
export const CHAOS_SERVER_ENV = {
  ...E2E_SERVER_ENV,
  MOCK_LLM_FAULTS:
    "analyst:timeout:3,explainer:error:3,talker:timeout:3,commentator:invalid_json:3," +
    "designer:error:3,recapper:timeout:3",
  LLM_TIMEOUT_S: "1",
  CLAIM_BUDGET_S: "3",
  RECAP_BUDGET_S: "3",
};
const PORT = Number(process.env.E2E_PORT ?? 8765);
const MOCK_PORT = Number(process.env.E2E_MOCK_PORT ?? 8766);
const CHAOS_PORT = Number(process.env.E2E_CHAOS_PORT ?? 8767);
const channel = process.env.CI ? undefined : (process.env.PW_CHANNEL ?? "msedge");
const server =
  process.env.E2E_SERVER ?? `uv run --project ../backend pundit serve --ui out --port ${PORT}`;
const chaosServer =
  process.env.E2E_CHAOS_SERVER ??
  `uv run --project ../backend pundit serve --ui out --port ${CHAOS_PORT}`;

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
        // Day 5 A10: a fake microphone (a tone), allowed without a prompt
        launchOptions: {
          args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
        },
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
    {
      name: "chaos",
      testDir: "tests/e2e-chaos",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `http://127.0.0.1:${CHAOS_PORT}`,
        ...(channel ? { channel } : {}),
        viewport: { width: 1600, height: 950 },
      },
    },
  ],
  webServer: [
    {
      command: server,
      env: E2E_SERVER_ENV,
      url: `http://127.0.0.1:${PORT}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: chaosServer,
      env: CHAOS_SERVER_ENV,
      url: `http://127.0.0.1:${CHAOS_PORT}/healthz`,
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
