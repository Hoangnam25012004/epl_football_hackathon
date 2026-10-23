// Day 6 (docs/day6-design.md 5.5, CH1-CH3): this backend's every LLM call fails
// (MOCK_LLM_FAULTS, playwright.config.ts). The studio, the fan player and the landing page still
// work: verdicts from the rules, template texts, the mock voice, the LLM's state in words.
import { expect, test } from "@playwright/test";
import {
  createMatch,
  stopMatch,
  storeProfile,
  submitClaim,
  waitStatus,
  watchErrors,
} from "../e2e/helpers";
import { audioState } from "../voice";

test.describe.configure({ mode: "serial" });

test("CH1 studio: a verdict from the rules, its template, talking points and lower-thirds", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const finished = await createMatch(request, { seed: 42, from_minute: 85 });
  await waitStatus(request, finished, "finished");
  await page.goto(`/studio/?match=${finished}`);
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "finished");
  const claim = await submitClaim(page, "Đội nhà áp đảo hiệp 2");
  expect(claim.verdict).toBe("TRUE");
  expect(claim.analyst_source).toBe("rule");
  expect(claim.explanation_source).toBe("template");
  const card = page.getByTestId("verdict-card").first();
  await expect(card.getByTestId("verdict")).toHaveAttribute("data-verdict", "TRUE");
  // the analyst failed (or its breaker is open): said in words on the card
  await expect(card).toContainText(/rule analyst used|LLM paused/);

  const live = await createMatch(request, { seed: 61, speed: 20, from_minute: 60 });
  await page.goto(`/studio/?match=${live}`);
  await expect
    .poll(() => page.getByTestId("talking-point").count(), { timeout: 60_000 })
    .toBeGreaterThan(0);
  await expect
    .poll(() => page.getByTestId("lower-third-item").count(), { timeout: 60_000 })
    .toBeGreaterThan(0);
  expect(errors).toEqual([]);
  await stopMatch(request, live);
});

test("CH2 fan: Vietnamese commentary, a half-time recap from its template, the voice reads", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const matchId = await createMatch(request, { seed: 62, speed: 20, from_minute: 41 });
  await storeProfile(page, { mode: "casual", language: "vi" });
  await page.goto(`/fan/?match=${matchId}`);
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "running");
  await page.getByTestId("voice-toggle").click();
  await expect(page.getByTestId("voice-toggle")).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(() => page.getByTestId("commentary-card").count(), { timeout: 60_000 })
    .toBeGreaterThan(1);
  await expect
    .poll(
      async () =>
        ((await audioState(page))?.played ?? []).filter((id) => !id.startsWith("greeting-")).length,
      { timeout: 90_000 },
    )
    .toBeGreaterThan(0);
  await page.getByTestId("tab-recap").click();
  await expect(page.getByTestId("recap-listen")).toBeVisible({ timeout: 90_000 });
  const recap = await (
    await request.get(`/matches/${matchId}/recap?scope=H1&lang=vi&mode=casual`)
  ).json();
  expect(recap.source).toBe("template");
  expect(recap.paragraphs.join(" ").length).toBeGreaterThan(40);
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("CH3 the landing page and /healthz say in words that the LLM is paused", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const matchId = await createMatch(request, { seed: 63, from_minute: 85 });
  await waitStatus(request, matchId, "finished");
  for (let i = 0; i < 2; i++) {
    // each claim fails twice (analyst, explainer): the breaker opens after three failures
    await request.post("/claims/verify", { data: { match_id: matchId, text: "Kestrel won" } });
  }
  const health = await (await request.get("/healthz")).json();
  expect(health.status).toBe("ok");
  expect(health.degraded).toContain("circuit_open");
  await page.goto("/");
  await expect(page.getByTestId("server-health")).toContainText(
    /LLM paused \(circuit open\)|tạm ngưng LLM/,
  );
  await expect(page.getByTestId("start-my-demo")).toBeEnabled();
  expect(errors).toEqual([]);
});
