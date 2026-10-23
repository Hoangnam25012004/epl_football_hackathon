// Day 6 demo run (docs/day6-design.md sections 8.1 and 8.7): the judge's path S1–S7 on one
// private demo match of the production build, with an assertion at each step and no console
// error anywhere. W14 runs it three times in a row (--repeat-each=3). Where a presenter would
// wait at 20x, the test speeds the replay up through the API; the steps are the same.
import { expect, test, type Page } from "@playwright/test";
import { audioState } from "../voice";
import { expectSynthetic, stopMatch, submitClaim, watchErrors } from "./helpers";

const PRIVATE = /\/studio\/\?match=(dominant_loser-42~[a-z0-9]{8})$/;

async function speedUp(page: Page, matchId: string, speed: number): Promise<void> {
  const response = await page.request.post(`/matches/${matchId}/speed`, { data: { speed } });
  expect(response.ok(), await response.text()).toBeTruthy();
}

test("S1–S7 the judge's demo on one private match, without a console error", async ({
  browser,
}) => {
  test.setTimeout(300_000);
  const context = await browser.newContext();
  const studio = await context.newPage();
  const watched = [watchErrors(studio)]; // console errors of every page, read at the end
  const status = studio.getByTestId("match-status");
  let matchId = "";

  await test.step("S1 landing → my demo match: a private studio at 20x", async () => {
    await studio.goto("/");
    await expectSynthetic(studio);
    await expect(studio.getByTestId("demo-steps")).toBeVisible();
    await studio.getByTestId("start-my-demo").click();
    await studio.waitForURL(PRIVATE);
    matchId = PRIVATE.exec(studio.url())?.[1] ?? "";
    await expect(studio.getByTestId("private-chip")).toBeVisible();
    await expectSynthetic(studio);
    await expect(studio.getByRole("radio", { name: "20x" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await studio.getByTestId("play-pause").click();
    await expect(status).toHaveAttribute("data-status", "running");
    await expect(studio).toHaveTitle("Studio · Pundit Copilot");
  });

  const fan = await context.newPage();
  watched.push(watchErrors(fan));
  await test.step("S5 fan: casual · vi · HAR · beginner, voice on, rules explained", async () => {
    await fan.goto(`/fan/?match=${matchId}`);
    await expectSynthetic(fan);
    await fan.getByTestId("mode-casual").click();
    await fan.getByRole("radio", { name: "Tiếng Việt" }).click();
    await fan.getByTestId("club-harbor_lions").click();
    await fan.getByTestId("beginner-setup").check();
    await fan.getByTestId("profile-start").click();
    await fan.getByTestId("voice-toggle").click();
    await expect(fan.getByTestId("voice-toggle")).toHaveAttribute("aria-pressed", "true");
    await expect(fan.getByTestId("voice-status")).toHaveText("Giọng thử nghiệm");
    // at 20x: the greeting, then live lines read aloud, one at a time (Day 5 A1 checks more)
    const live = async () =>
      ((await audioState(fan))?.played ?? []).filter((id) => !id.startsWith("greeting-"));
    await expect.poll(async () => (await live()).length, { timeout: 90_000 }).toBeGreaterThan(0);
    expect((await audioState(fan))?.maxConcurrent).toBe(1);
    // faster from here: a rule is explained the first time it happens (beginner mode)
    await speedUp(studio, matchId, 120);
    await expect(fan.getByTestId("rule-explanation").first()).toBeVisible({ timeout: 90_000 });
  });

  await test.step("S2 to full time, then a claim: verdict with colour, icon, words, numbers", async () => {
    await speedUp(studio, matchId, 1000);
    await expect(status).toHaveAttribute("data-status", "finished", { timeout: 90_000 });
    const claim = await submitClaim(studio, "Đội nhà áp đảo hiệp 2");
    expect(claim.verdict).toBe("TRUE");
    expect(claim.match_id).toBe(matchId);
    const card = studio.getByTestId("verdict-card").first();
    await expect(card.getByTestId("verdict")).toHaveAttribute("data-verdict", "TRUE");
    await expect(card.getByTestId("verdict")).toContainText("✓");
    const facts = await card.getByTestId("fact").allTextContents();
    expect(facts.join(" ")).toMatch(/61%/);
    expect(facts.join(" ")).toMatch(/\b15\b/);
  });

  await test.step("S6 the recap tab: listen", async () => {
    // the clips are made in the background when a recap is published
    for (const scope of ["H1", "FULL"]) {
      const recap = `/matches/${matchId}/recap?scope=${scope}&lang=vi&mode=casual&voice=female`;
      await expect
        .poll(async () => (await (await fan.request.get(recap)).json()).speech?.audio?.exact, {
          timeout: 30_000,
        })
        .toBe(true);
    }
    await fan.getByTestId("tab-recap").click();
    await fan.getByTestId("recap-listen").click();
    const player = fan.getByTestId("recap-player");
    await expect.poll(() => player.evaluate((el: HTMLAudioElement) => !el.paused)).toBe(true);
  });

  await test.step("S3 Why · Evidence · Agent Trace, and the help", async () => {
    await studio.getByTestId("verdict-card").first().getByTestId("claim-details").click();
    await expect(studio.getByTestId("drawer-why")).toContainText("Đội nhà áp đảo hiệp 2");
    await studio.getByTestId("drawer-tab-evidence").click();
    await expect(studio.getByTestId("evidence-events").locator("li").first()).toBeVisible();
    await studio.getByTestId("drawer-tab-trace").click();
    await expect.poll(() => studio.getByTestId("trace-span").count()).toBeGreaterThan(5);
    await expect(studio.getByTestId("trace-gantt")).toContainText("Guardrail");
    await studio.keyboard.press("Escape");
    await expect(studio.getByTestId("drawer")).toHaveCount(0);
    await studio.keyboard.press("?");
    await expect(studio.getByTestId("help")).toBeVisible();
    await studio.keyboard.press("Escape");
    await expect(studio.getByTestId("help")).toHaveCount(0);
  });

  await test.step("S4 pin and approve a talking point; a lower-third on air, then off", async () => {
    const points = studio.getByTestId("talking-point");
    await expect(points.nth(1)).toBeVisible();
    const [first, second] = await points.evaluateAll((items) =>
      items.slice(0, 2).map((item) => item.getAttribute("data-id") ?? ""),
    );
    const point = (id: string) => studio.locator(`[data-testid=talking-point][data-id="${id}"]`);
    await point(first!).getByTestId("tp-pin").click();
    await expect(point(first!)).toHaveAttribute("data-status", "pinned");
    await point(second!).getByTestId("tp-approve").click();
    await studio.getByTestId("tp-tab-approved").click();
    await expect(point(second!)).toHaveAttribute("data-status", "approved");

    const overlay = await context.newPage();
    watched.push(watchErrors(overlay));
    await overlay.goto(`/overlay/?match=${matchId}&lang=en&debug=1`);
    await expect(overlay.getByTestId("overlay-root")).toHaveAttribute("data-connection", "open");
    const takeable = studio
      .getByTestId("lower-third-item")
      .filter({ has: studio.locator("[data-testid=on-air-take]:not([disabled])") });
    await expect(takeable.first()).toBeVisible();
    const takenId = await takeable.first().getAttribute("data-id");
    await takeable.first().getByTestId("on-air-take").click();
    await expect
      .poll(() => overlay.evaluate(() => window.punditOverlay?.current?.id))
      .toBe(takenId);
    await expect(overlay.getByTestId("lower-third")).toBeVisible();
    await expect(overlay.getByTestId("lt-synthetic")).toBeVisible();
    await studio.getByTestId("on-air-clear").click();
    await expect(overlay.getByTestId("lower-third")).toHaveCount(0);
    await overlay.close();
  });

  await test.step("S7 Analyst and Casual: the same moments, two ways of writing them", async () => {
    const compare = await context.newPage();
    watched.push(watchErrors(compare));
    await compare.goto(`/compare/?match=${matchId}`);
    await expectSynthetic(compare);
    await expect(compare.getByTestId("compare-row").first()).toBeVisible();
    const pairs = await compare
      .getByTestId("compare-row")
      .evaluateAll((rows) =>
        rows
          .map((row) =>
            [...row.querySelectorAll("[data-testid=commentary-text]")].map((t) => t.textContent),
          )
          .filter((texts) => texts.length === 2),
      );
    expect(pairs.length).toBeGreaterThan(5);
    expect(pairs.every(([left, right]) => left !== right)).toBe(true);
    await compare.close();
  });

  expect(watched.flat()).toEqual([]);
  await stopMatch(studio.request, matchId);
  await context.close();
});
