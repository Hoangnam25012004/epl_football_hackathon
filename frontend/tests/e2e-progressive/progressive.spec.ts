// Day 7 (docs/day7-design.md sections 2.4-2.6): this backend answers a claim twice, first from
// the rules, then from the mock model slowed to 1.5 s a call (PROGRESSIVE_SERVER_ENV).
// PR1 rev.1 comes first, rev.2 replaces its chip and explanation; PR2 Agent Ops lights the
// claim lane in order; PR3 Chaos in a private match: the model lies, no wrong number reaches
// a screen, and a shared match is locked; PR4 the new page passes axe.
import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  createMatch,
  seriousA11y,
  stopMatch,
  submitClaim,
  waitStatus,
  watchErrors,
} from "../e2e/helpers";

test.describe.configure({ mode: "serial" });

const CLAIM = "Đội nhà áp đảo hiệp 2";

async function privateMatch(request: APIRequestContext): Promise<string> {
  const response = await request.post("/matches", {
    data: { scenario: "dominant_loser", seed: 42, speed: 1000, private: true },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { match_id: string }).match_id;
}

test("PR1 rev.1 from the rules at once, then rev.2 from the model replaces it", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const matchId = await createMatch(request, { seed: 42 });
  await waitStatus(request, matchId, "finished");
  await page.goto(`/studio/?match=${matchId}`);
  const claim = await submitClaim(page, CLAIM);
  expect(claim.revision).toBe(1);
  expect(claim.model_pass).toBe("queued");
  const card = page.getByTestId("verdict-card").first();
  await expect(card.getByTestId("verdict")).toHaveAttribute("data-verdict", "TRUE");
  await expect(card.getByTestId("claim-source").first()).toContainText(/^Rules · \d+\.\d s$/);
  await expect(card.getByRole("status")).toContainText(/Model check queued|The model is checking/);
  await expect(card).toHaveAttribute("data-revision", "2", { timeout: 30_000 });
  await expect(card).toHaveAttribute("data-model-pass", "done");
  await expect(card.getByTestId("claim-source").first()).toContainText(/^Model · mock · live/);
  await expect(card.getByTestId("claim-note")).toContainText("The model read it as the rules did");
  await expect(card.getByRole("status")).toHaveCount(0);
  await expect(card.getByTestId("verdict")).toHaveAttribute("data-verdict", "TRUE");
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("PR2 Agent Ops: a claim lights the claim lane in order, with its handoffs", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const matchId = await createMatch(request, { seed: 42 });
  await waitStatus(request, matchId, "finished");
  await page.goto(`/agents/?match=${matchId}`);
  await expect(page.getByTestId("agent-lanes")).toBeVisible();
  await expect(page).toHaveTitle(/Agent Ops/);
  const response = await request.post("/claims/verify", {
    data: { match_id: matchId, text: CLAIM },
  });
  expect(response.ok()).toBeTruthy();
  const node = (agent: string) =>
    page.locator(`[data-testid="agent-node"][data-workflow="claim"][data-agent="${agent}"]`);
  for (const agent of ["ClaimIntake", "RuleAnalyst", "EvidenceCollector", "Judge", "Publisher"])
    await expect(node(agent)).toHaveAttribute("data-status", "ok", { timeout: 30_000 });
  await expect(page.getByTestId("handoff-log")).toContainText("ClaimIntake → ClaimContext");
  await expect(page.getByTestId("handoff-log")).toContainText("Judge → ExplainRequest");
  await expect(page.getByTestId("shared-state")).toContainText(/MatchState @ seq \d+ · /);
  await node("Judge").click();
  await expect(page.getByTestId("agent-detail")).toContainText("Judge");
  await expect(page.getByTestId("agent-table")).toContainText("Verdict from the facts");
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("PR3 Chaos: the model lies in my match, the guardrail blocks it; a shared one is locked", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const shared = await createMatch(request, { seed: 42 });
  await page.goto(`/agents/?match=${shared}`);
  await expect(page.getByTestId("chaos-locked")).toBeVisible();

  const mine = await privateMatch(request);
  await waitStatus(request, mine, "finished");
  await page.goto(`/agents/?match=${mine}`);
  await expect(page.getByTestId("chaos-panel")).toBeVisible();
  await page.getByTestId("chaos-model_lies").click();
  await expect(page.getByTestId("chaos-state")).toContainText("The model lies", {
    timeout: 10_000,
  });
  for (let n = 0; n < 2; n += 1) {
    const response = await request.post("/claims/verify", {
      data: { match_id: mine, text: CLAIM },
    });
    expect(response.ok()).toBeTruthy();
  }
  await expect(page.getByTestId("chaos-lies")).toContainText(
    /Wrong numbers that reached a screen: 0 \(the model lied [1-9]\d* times\)/,
    { timeout: 30_000 },
  );
  const guardrail = page.locator(
    '[data-testid="agent-node"][data-workflow="claim"][data-agent="Guardrail"]',
  );
  await expect(page.getByTestId("handoff-log")).toContainText("Guardrail rejected a draft");
  await expect(guardrail).toBeVisible();
  await page.getByTestId("chaos-reset").click();
  await expect(page.getByTestId("chaos-state")).toContainText("Nothing is broken.");
  expect(errors).toEqual([]);
  await stopMatch(request, shared);
  await stopMatch(request, mine);
});

test("PR4 Agent Ops passes axe and works at phone width", async ({ page, request }) => {
  const matchId = await createMatch(request, { seed: 42 });
  await waitStatus(request, matchId, "finished");
  await request.post("/claims/verify", { data: { match_id: matchId, text: CLAIM } });
  await page.goto(`/agents/?match=${matchId}`);
  await expect(page.getByTestId("agent-lanes")).toBeVisible();
  expect(await seriousA11y(page)).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  const scroll = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(scroll).toBeLessThanOrEqual(0);
  expect(await seriousA11y(page)).toEqual([]);
  await stopMatch(request, matchId);
});
