// Day 4 E2E on the real backend (mock LLM) serving the static build: docs/day4-design.md §12 F5,
// scenarios E1–E12 (E11 drops the connection, so it runs in the `mock` project). Claims and their
// answers come from the Day 2 eval set (backend/evals/claims_dominant_loser_42.json).
import { expect, test, type Page } from "@playwright/test";
import {
  createMatch,
  expectSynthetic,
  matchInfo,
  socketStats,
  stopMatch,
  storeProfile,
  submitClaim,
  waitStatus,
  watchErrors,
} from "./helpers";

test("E1 landing → studio: scoreboard, controls, synthetic label, no console error", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expectSynthetic(page);
  await expect(page.getByTestId("scenario-dominant_loser")).toBeVisible();
  await page.getByTestId("open-studio-dominant_loser").click();
  await expect(page).toHaveURL(/\/studio\/\?match=dominant_loser-42$/);
  await expect(page.getByTestId("scoreboard")).toContainText("HAR");
  await expect(page.getByTestId("scoreboard")).toContainText("KES");
  await expect(page.getByTestId("replay-controls")).toBeVisible();
  await expect(page.getByTestId("match-status")).toBeVisible();
  await expectSynthetic(page);
  expect(errors).toEqual([]);
});

test("E1b the landing page lists running matches without a console error (no 404 prefetch)", async ({
  page,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 59, speed: 20 });
  const errors = watchErrors(page);
  await page.goto("/");
  const row = page.getByText(matchId, { exact: true });
  await expect(row).toBeVisible();
  await row.scrollIntoViewIfNeeded();
  await page.waitForTimeout(1500); // a prefetch would have happened by now
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("E2 play 20x, pause stops events, resume, 5x; n continuous, no duplicate event", async ({
  page,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 43, speed: 20, autostart: false });
  await page.goto(`/studio/?match=${matchId}`);
  const status = page.getByTestId("match-status");
  await expect(status).toHaveAttribute("data-status", "created");
  await expect(page.getByRole("radio", { name: "20x" })).toHaveAttribute("aria-checked", "true");

  await page.getByTestId("play-pause").click();
  await expect(status).toHaveAttribute("data-status", "running");
  await expect.poll(() => page.getByTestId("event-row").count()).toBeGreaterThan(20);

  await page.getByTestId("play-pause").click();
  await expect(status).toHaveAttribute("data-status", "paused");
  const paused = (await matchInfo(request, matchId)).events_applied;
  await page.waitForTimeout(1500); // nothing may happen while paused
  expect((await matchInfo(request, matchId)).events_applied).toBe(paused);

  await page.getByTestId("play-pause").click();
  await expect(status).toHaveAttribute("data-status", "running");
  await expect
    .poll(async () => (await matchInfo(request, matchId)).events_applied)
    .toBeGreaterThan(paused);

  const fiveX = page.getByRole("radio", { name: "5x" });
  await fiveX.click();
  await expect(fiveX).toHaveAttribute("aria-checked", "true");
  expect((await matchInfo(request, matchId)).speed).toBe(5);

  const ids = await page
    .getByTestId("event-row")
    .evaluateAll((rows) => rows.map((row) => row.getAttribute("data-id")));
  expect(new Set(ids).size).toBe(ids.length);
  expect(await socketStats(page, "studio")).toMatchObject({
    gaps: 0,
    duplicates: 0,
    badFrames: 0,
  });
  await stopMatch(request, matchId);
});

test("E3 a TRUE claim shows the server's numbers, then Why / Evidence / Agent Trace", async ({
  page,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 42, from_minute: 85 });
  await waitStatus(request, matchId, "finished");
  await page.goto(`/studio/?match=${matchId}`);
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "finished");

  const claim = await submitClaim(page, "Đội nhà áp đảo hiệp 2");
  expect(claim.verdict).toBe("TRUE");
  const card = page.getByTestId("verdict-card").first();
  await expect(card.getByTestId("verdict")).toHaveAttribute("data-verdict", "TRUE");
  // U1: the card shows the server's headline `display` strings, in the server's order
  const displayOf = (chip: string) => chip.slice(chip.lastIndexOf(": ") + 2);
  const shown = (await card.getByTestId("fact").allTextContents()).map(displayOf);
  expect(shown).toEqual(claim.headline.slice(0, 6).map((fact) => fact.display));
  expect(shown).toEqual(expect.arrayContaining(["61%", "15", "2.24"]));

  // the Why tab has both sides of the comparison: 61%–39%, 15–1, 2.24–0.24 (eval e01)
  await card.getByTestId("claim-details").click();
  const why = page.getByTestId("drawer-why");
  await expect(why).toContainText("Đội nhà áp đảo hiệp 2");
  const both = (await why.getByTestId("assertion").getByTestId("fact").allTextContents()).map(
    displayOf,
  );
  expect(both).toEqual(expect.arrayContaining(["61%", "39%", "15", "1", "2.24", "0.24"]));
  await page.getByTestId("drawer-tab-evidence").click();
  await expect.poll(() => page.getByTestId("pitch-point").count()).toBeGreaterThan(5);
  await expect(page.getByTestId("evidence-events").locator("li").first()).toBeVisible();
  await page.getByTestId("drawer-tab-trace").click();
  await expect.poll(() => page.getByTestId("trace-span").count()).toBeGreaterThan(5);
  await expect(page.getByTestId("trace-gantt")).toContainText("Guardrail");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("drawer")).toHaveCount(0);
});

test("E4 FALSE, PARTLY_TRUE and INSUFFICIENT_DATA each have a colour, an icon and words", async ({
  page,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 42 });
  await waitStatus(request, matchId, "finished");
  await page.goto(`/studio/?match=${matchId}`);
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "finished");
  const cases = [
    ["HAR kiểm soát bóng 70% trong hiệp 2", "FALSE", "False"],
    ["HAR áp đảo hiệp 2 và thắng trận", "PARTLY_TRUE", "Partly true"],
    ["Trọng tài thiên vị Harbor", "INSUFFICIENT_DATA", "Not enough data"],
  ] as const;
  for (const [text, verdict, words] of cases) {
    const claim = await submitClaim(page, text);
    expect(claim.verdict).toBe(verdict);
    const pill = page
      .getByTestId("verdict-card")
      .filter({ hasText: text })
      .first()
      .getByTestId("verdict");
    await expect(pill).toHaveAttribute("data-verdict", verdict);
    await expect(pill).toContainText(words);
    await expect(pill.locator("[aria-hidden]")).not.toBeEmpty();
  }
  const colours = await page
    .getByTestId("verdict")
    .evaluateAll(
      (pills) => new Set(pills.map((pill) => getComputedStyle(pill).backgroundColor)).size,
    );
  expect(colours).toBe(3);
});

test("E5 approve / pin / dismiss in one studio reaches another studio in under a second", async ({
  browser,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 44 });
  await waitStatus(request, matchId, "finished");
  const a = await (await browser.newContext()).newPage();
  const b = await (await browser.newContext()).newPage();
  await a.goto(`/studio/?match=${matchId}`);
  await b.goto(`/studio/?match=${matchId}`);
  const point = (page: Page, id: string) =>
    page.locator(`[data-testid=talking-point][data-id="${id}"]`);
  const points = a.getByTestId("talking-point");
  await expect(points.nth(1)).toBeVisible();
  const [first, second] = await points.evaluateAll((items) =>
    items.slice(0, 2).map((item) => item.getAttribute("data-id")!),
  );

  await point(a, first!).getByTestId("tp-pin").click();
  await expect(point(b, first!)).toHaveAttribute("data-status", "pinned", { timeout: 1000 });

  await point(a, first!).getByTestId("tp-approve").click();
  await b.getByTestId("tp-tab-approved").click();
  await expect(point(b, first!)).toHaveAttribute("data-status", "approved", { timeout: 1000 });

  await point(a, second!).getByTestId("tp-dismiss").click();
  await b.getByTestId("tp-tab-dismissed").click();
  await expect(point(b, second!)).toHaveAttribute("data-status", "dismissed", { timeout: 1000 });

  // and back: B restores the dismissed one, A sees it in its queue again
  await point(b, second!).getByTestId("tp-restore").click();
  await expect(point(a, second!)).toHaveAttribute("data-status", "new", { timeout: 1000 });
  await a.context().close();
  await b.context().close();
});

test("E6 + E7 the overlay runs by itself, then obeys Take / Hold / Clear / Auto", async ({
  browser,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 42, speed: 60, from_minute: 24 });
  const overlay = await (
    await browser.newContext({ viewport: { width: 1280, height: 720 } })
  ).newPage();
  const errors = watchErrors(overlay);
  await overlay.goto(`/overlay/?match=${matchId}&lang=en`);
  const lowerThird = overlay.getByTestId("lower-third");
  await expect(lowerThird).toBeVisible({ timeout: 45_000 });
  await expect(lowerThird).toHaveClass(/lt-enter/);
  await expect(lowerThird.getByTestId("lt-synthetic")).toBeVisible();
  const published = await overlay.evaluate(() =>
    JSON.parse(document.getElementById("pundit-on-air")!.textContent!),
  );
  expect(published.current).toMatchObject({
    match_clock: expect.any(String),
    show_at_match_ms: expect.any(Number),
    duration_ms: expect.any(Number),
  });
  const firstId: string = published.current.id;
  // it goes off air by itself (or a newer one replaces it)
  await expect
    .poll(() => overlay.evaluate(() => window.punditOverlay?.current?.id ?? null), {
      timeout: 30_000,
    })
    .not.toBe(firstId);

  const studio = await (await browser.newContext()).newPage();
  await studio.goto(`/studio/?match=${matchId}`);
  const takeable = studio
    .getByTestId("lower-third-item")
    .filter({ has: studio.locator("[data-testid=on-air-take]:not([disabled])") });
  await expect(takeable.nth(1)).toBeVisible();
  const old = takeable.last(); // an older graphic, taken back on air by hand
  const takenId = await old.getAttribute("data-id");
  await old.getByTestId("on-air-take").click();
  await expect.poll(() => overlay.evaluate(() => window.punditOverlay?.current?.id)).toBe(takenId);
  await expect
    .poll(() => overlay.evaluate(() => window.punditOverlay?.on_air?.mode))
    .toBe("manual");
  await expect(studio.getByTestId("on-air-mode")).toHaveText("MANUAL");

  await studio.getByTestId("on-air-hold").click();
  await expect.poll(() => overlay.evaluate(() => window.punditOverlay?.on_air?.hold)).toBe(true);
  await expect(studio.getByTestId("on-air-mode")).toHaveText("HOLD");
  await studio.getByTestId("on-air-release").click();
  await expect.poll(() => overlay.evaluate(() => window.punditOverlay?.on_air?.hold)).toBe(false);

  await studio.getByTestId("on-air-clear").click();
  await expect.poll(() => overlay.evaluate(() => window.punditOverlay?.current ?? null)).toBeNull();
  await expect(overlay.getByTestId("lower-third")).toHaveCount(0);

  await studio.getByTestId("on-air-auto").click();
  await expect.poll(() => overlay.evaluate(() => window.punditOverlay?.on_air?.mode)).toBe("auto");
  await expect(studio.getByTestId("on-air-mode")).toHaveText("AUTO");
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
  await overlay.context().close();
  await studio.context().close();
});

test("E8 + E9 fan: casual · vi · HAR · beginner, then analyst · en replaces the feed", async ({
  page,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 42, speed: 120 });
  await page.goto(`/fan/?match=${matchId}`);
  await expectSynthetic(page);
  await page.getByTestId("mode-casual").click();
  await page.getByRole("radio", { name: "Tiếng Việt" }).click();
  await page.getByTestId("club-harbor_lions").click();
  await page.getByTestId("beginner-setup").check();
  await page.getByTestId("profile-start").click();

  const vietnamese = page.locator("[data-testid=commentary-text][lang=vi]");
  const english = page.locator("[data-testid=commentary-text][lang=en]");
  await expect(page.getByTestId("rule-explanation").first()).toBeVisible({ timeout: 60_000 });
  await expect(vietnamese.first()).toBeVisible();
  await expect(english).toHaveCount(0);
  await expect(page.getByTestId("commentary-framing").first()).toBeVisible();
  await expect(
    page.locator("[data-testid=commentary-card][data-emphasis=own_team]").first(),
  ).toBeVisible();

  await page.getByTestId("change-profile").click();
  await page.getByTestId("mode-analyst").click();
  await page.getByRole("radio", { name: "English" }).click();
  await page.getByTestId("profile-start").click();
  await expect(english.first()).toBeVisible();
  await expect(vietnamese).toHaveCount(0);
  const texts = await english.allTextContents();
  expect(texts.some((text) => /\d/.test(text))).toBeTruthy();
  await stopMatch(request, matchId);
});

test("E10 compare: the same moment side by side — same id and minute, other words", async ({
  page,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 45 });
  await waitStatus(request, matchId, "finished");
  await page.goto(`/compare/?match=${matchId}`);
  await expectSynthetic(page);
  await expect(page.getByTestId("compare-row").first()).toBeVisible();
  const pairs = await page.getByTestId("compare-row").evaluateAll((rows) =>
    rows
      .map((row) =>
        [...row.querySelectorAll("[data-testid=commentary-card]")].map((card) => ({
          id: card.getAttribute("data-id"),
          minute: card.querySelector("[data-testid=commentary-minute]")?.textContent,
          text: card.querySelector("[data-testid=commentary-text]")?.textContent,
          lang: card.querySelector("[data-testid=commentary-text]")?.getAttribute("lang"),
        })),
      )
      .filter((cards) => cards.length === 2),
  );
  expect(pairs.length).toBeGreaterThan(10);
  for (const [left, right] of pairs) {
    expect(left!.id).toBe(right!.id);
    expect(left!.minute).toBe(right!.minute);
    expect(left!.text).not.toBe(right!.text);
    expect([left!.lang, right!.lang]).toEqual(["en", "vi"]);
  }
});

test("E12 the studio restarts from 45': the open overlay and fan follow the new session", async ({
  browser,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 46, speed: 20 });
  const studio = await (await browser.newContext()).newPage();
  const fan = await (await browser.newContext()).newPage();
  const overlay = await (await browser.newContext()).newPage();
  await storeProfile(fan, { mode: "casual", language: "vi" });
  await studio.goto(`/studio/?match=${matchId}`);
  await fan.goto(`/fan/?match=${matchId}`);
  await overlay.goto(`/overlay/?match=${matchId}&lang=vi`);
  await expect(fan.getByTestId("match-status")).toHaveAttribute("data-status", "running");
  await expect(overlay.getByTestId("overlay-root")).toHaveAttribute("data-connection", "open");

  studio.once("dialog", (dialog) => void dialog.accept());
  await studio.getByTestId("restart-from").selectOption("45");
  for (const [page, name] of [
    [fan, "fan"],
    [overlay, "overlay"],
  ] as const) {
    await expect
      .poll(async () => (await socketStats(page, name))?.reconnects ?? 0, { timeout: 10_000 })
      .toBeGreaterThan(0);
  }
  const secondHalf = /^(4[5-9]|[5-9]\d)/;
  await expect(fan.getByTestId("clock")).toHaveText(secondHalf, { timeout: 20_000 });
  await expect(studio.getByTestId("clock")).toHaveText(secondHalf, { timeout: 20_000 });
  await expect(overlay.getByTestId("overlay-root")).toHaveAttribute("data-connection", "open");
  expect((await matchInfo(request, matchId)).status).toBe("running");
  await stopMatch(request, matchId);
  for (const page of [studio, fan, overlay]) await page.context().close();
});
