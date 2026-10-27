// M10 (docs/day7-design.md sections 3.11-3.12): the judge's path S1-S9 on the public Space, in
// one private demo match per browser. The Space runs a real model on 2 CPUs, so rev.2 may come
// from the model or say why it did not (both are what a judge can see); the voice is the
// browser's own. Where a presenter would wait at 20x, the test speeds the replay up.
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { expectSynthetic, submitClaim, watchErrors } from "../e2e/helpers";

const PRIVATE = /\/studio\/\?match=(dominant_loser-42~[a-z0-9]{8})$/;
const CLAIM = "Đội nhà áp đảo hiệp 2";

async function speedUp(page: Page, matchId: string, speed: number): Promise<void> {
  const response = await page.request.post(`/matches/${matchId}/speed`, { data: { speed } });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function serious(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.length} × ${v.nodes[0]?.target.join(" ")}`);
}

/** S1: the landing page, its status line in plain words, then my private demo match. */
async function startDemo(page: Page): Promise<string> {
  await page.goto("/");
  await expectSynthetic(page);
  const status = page.getByTestId("server-health");
  await expect(status).toContainText("qwen2.5:1.5b");
  await expect(status).not.toContainText(/mock/i);
  await page.getByTestId("start-my-demo").click();
  await page.waitForURL(PRIVATE, { timeout: 60_000 });
  const matchId = PRIVATE.exec(page.url())?.[1] ?? "";
  await expect(page.getByTestId("private-chip")).toBeVisible();
  await expectSynthetic(page);
  return matchId;
}

/** S2: a claim answered by the rules at once, then rev.2 (the model's, or why not). */
async function claimTwice(page: Page, matchId: string): Promise<void> {
  await page.getByTestId("play-pause").click();
  await speedUp(page, matchId, 1000);
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "finished", {
    timeout: 120_000,
  });
  const began = Date.now();
  const claim = await submitClaim(page, CLAIM);
  expect(Date.now() - began).toBeLessThan(5_000); // rev.1, through the internet
  expect(claim.verdict).toBe("TRUE");
  expect(claim.revision).toBe(1);
  const card = page.getByTestId("verdict-card").first();
  await expect(card.getByTestId("verdict")).toHaveAttribute("data-verdict", "TRUE");
  const facts = (await card.getByTestId("fact").allTextContents()).join(" ");
  expect(facts).toMatch(/61%/);
  expect(facts).toMatch(/\b15\b/);
  await expect(card.getByTestId("claim-source").first()).toContainText(/^(Rules|Luật) · /);
  await expect(card).toHaveAttribute("data-revision", "2", { timeout: 200_000 });
  await expect(card.getByTestId("verdict")).toHaveAttribute("data-verdict", "TRUE");
  const pass = await card.getAttribute("data-model-pass");
  expect(["done", "skipped"]).toContain(pass);
  if (pass === "skipped")
    await expect(card).toContainText(/the rules' answer stands|câu trả lời theo luật giữ nguyên/);
  await expect(card).not.toContainText(/mock/i);
}

test("S1-S9 on Edge at 1366×768 @edge", async ({ browser }) => {
  const context = await browser.newContext();
  const studio = await context.newPage();
  const errors = [watchErrors(studio)];
  let matchId = "";
  await test.step("S1 landing → my demo match", async () => {
    matchId = await startDemo(studio);
    expect(await serious(studio)).toEqual([]);
  });
  await test.step("S2 the claim: rules now, the model after", async () => {
    await claimTwice(studio, matchId);
  });
  await test.step("S3 Why · Evidence · Agent Trace", async () => {
    await studio.getByTestId("verdict-card").first().getByTestId("claim-details").click();
    await expect(studio.getByTestId("drawer-why")).toContainText(CLAIM);
    await studio.getByTestId("drawer-tab-evidence").click();
    await expect(studio.getByTestId("evidence-events").locator("li").first()).toBeVisible();
    await studio.getByTestId("drawer-tab-trace").click();
    await expect(studio.getByTestId("trace-gantt")).toContainText("Guardrail");
    await studio.keyboard.press("Escape");
  });
  await test.step("S4 a talking point pinned; a lower-third on air and off", async () => {
    const point = studio.getByTestId("talking-point").first();
    await point.getByTestId("tp-pin").click();
    await expect(point).toHaveAttribute("data-status", "pinned");
    const overlay = await context.newPage();
    errors.push(watchErrors(overlay));
    await overlay.goto(`/overlay/?match=${matchId}&lang=en&debug=1`);
    await expect(overlay.getByTestId("overlay-root")).toHaveAttribute("data-connection", "open");
    const takeable = studio
      .getByTestId("lower-third-item")
      .filter({ has: studio.locator("[data-testid=on-air-take]:not([disabled])") });
    await takeable.first().getByTestId("on-air-take").click();
    await expect(overlay.getByTestId("lower-third")).toBeVisible();
    await expect(overlay.getByTestId("lt-synthetic")).toBeVisible();
    await studio.getByTestId("on-air-clear").click();
    await expect(overlay.getByTestId("lower-third")).toHaveCount(0);
    await overlay.close();
  });
  await test.step("S5 + S6 fan: casual · vi · HAR · beginner, the recap", async () => {
    const fan = await context.newPage();
    errors.push(watchErrors(fan));
    await fan.goto(`/fan/?match=${matchId}`);
    await fan.getByTestId("mode-casual").click();
    await fan.getByRole("radio", { name: "Tiếng Việt" }).click();
    await fan.getByTestId("club-harbor_lions").click();
    await fan.getByTestId("beginner-setup").check();
    await fan.getByTestId("profile-start").click();
    await fan.getByTestId("voice-toggle").click();
    await expect(fan.getByTestId("voice-toggle")).toHaveAttribute("aria-pressed", "true");
    // the browser's own voice, or says plainly this browser has no Vietnamese voice (a
    // headless Edge has none; the person checks by ear on a real phone and browser)
    await expect(fan.getByTestId("voice-status")).toContainText(/trình duyệt/i);
    await expect(fan.getByTestId("rule-explanation").first()).toBeVisible();
    await fan.getByTestId("tab-recap").click();
    await expect(fan.getByTestId("recap-panel")).not.toBeEmpty();
    expect(await serious(fan)).toEqual([]);
    await fan.close();
  });
  await test.step("S7 Analyst and Casual side by side", async () => {
    const compare = await context.newPage();
    errors.push(watchErrors(compare));
    await compare.goto(`/compare/?match=${matchId}`);
    await expect(compare.getByTestId("compare-row").first()).toBeVisible();
    await compare.close();
  });
  const agents = await context.newPage();
  errors.push(watchErrors(agents));
  await test.step("S8 Agent Ops: a claim lights the claim lane", async () => {
    await agents.goto(`/agents/?match=${matchId}`);
    await expect(agents.getByTestId("agent-lanes")).toBeVisible();
    await submitClaim(studio, "Kestrel took more shots than Harbor");
    const node = (agent: string) =>
      agents.locator(`[data-testid=agent-node][data-workflow=claim][data-agent=${agent}]`);
    for (const agent of ["ClaimIntake", "RuleAnalyst", "Judge", "Publisher"])
      await expect(node(agent)).toHaveAttribute("data-status", "ok");
    await expect(agents.getByTestId("handoff-log")).toContainText("ClaimIntake → ClaimContext");
    await expect(agents.getByTestId("shared-state")).toContainText(/MatchState @ seq \d+/);
    expect(await serious(agents)).toEqual([]);
  });
  await test.step("S9 Chaos: the model lies, then is down; reset", async () => {
    await agents.getByTestId("chaos-model_lies").click();
    await expect(agents.getByTestId("chaos-state")).toContainText("The model lies");
    await submitClaim(studio, CLAIM);
    await expect(studio.getByTestId("verdict-card").first()).toHaveAttribute("data-revision", "2", {
      timeout: 200_000,
    });
    await expect(agents.getByTestId("chaos-lies")).toContainText(
      "Wrong numbers that reached a screen: 0",
    );
    await agents.getByTestId("chaos-model_down").click();
    await expect(agents.getByTestId("chaos-state")).toContainText("Model down");
    const down = await submitClaim(studio, CLAIM);
    expect(down.verdict).toBe("TRUE"); // rev.1 from the rules, whatever the model does
    await agents.getByTestId("chaos-reset").click();
    await expect(agents.getByTestId("chaos-state")).toContainText("Nothing is broken.");
  });
  await studio.request.post(`/matches/${matchId}/stop`);
  expect(errors.flat()).toEqual([]);
  await context.close();
});

test("S1, S2, S5 on Chrome @chrome", async ({ page }) => {
  const errors = watchErrors(page);
  const matchId = await startDemo(page);
  await claimTwice(page, matchId);
  await page.goto(`/fan/?match=${matchId}`);
  await page.getByTestId("profile-start").click();
  await page.getByTestId("voice-toggle").click();
  await expect(page.getByTestId("voice-toggle")).toHaveAttribute("aria-pressed", "true");
  await page.request.post(`/matches/${matchId}/stop`);
  expect(errors).toEqual([]);
});

test("the phone: the landing page, the fan's voice by a tap, Agent Ops vertical @phone", async ({
  page,
}) => {
  const errors = watchErrors(page);
  const noSideScroll = async () =>
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(0);
  const matchId = await startDemo(page);
  await noSideScroll();
  await page.goto(`/fan/?match=${matchId}`);
  await page.getByTestId("profile-start").tap();
  await page.getByTestId("voice-toggle").tap();
  await expect(page.getByTestId("voice-toggle")).toHaveAttribute("aria-pressed", "true");
  await noSideScroll();
  await page.goto(`/agents/?match=${matchId}`);
  await expect(page.getByTestId("agent-lanes")).toBeVisible();
  await noSideScroll();
  await page.request.post(`/matches/${matchId}/stop`);
  expect(errors).toEqual([]);
});
