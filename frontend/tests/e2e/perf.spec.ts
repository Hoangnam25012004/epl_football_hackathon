// F7 (docs/day4-design.md section 12): a whole match at 1000x with the studio, a fan and the
// overlay open — no long task over 200 ms and less than 50 MB of heap growth per tab; at 20x the
// message → store latency p95 stays under 100 ms. Chromium only (long tasks, CDP heap usage).
import { expect, test, type Page } from "@playwright/test";
import { createMatch, socketStats, stopMatch, storeProfile, waitStatus } from "./helpers";

declare global {
  interface Window {
    longTasks?: number[];
  }
}

// shared CI runners are too noisy for time budgets; F7 is measured on a developer machine
test.skip(!!process.env.CI, "time budgets run locally (docs/day4-design.md section 12 F7)");

function observeLongTasks() {
  window.longTasks = [];
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) window.longTasks!.push(entry.duration);
  }).observe({ type: "longtask", buffered: true });
}

async function heapMb(page: Page): Promise<number> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("HeapProfiler.collectGarbage");
  const { usedSize } = await cdp.send("Runtime.getHeapUsage");
  await cdp.detach();
  return usedSize / 2 ** 20;
}

test("F7 a whole match at 1000x: no long task over 200 ms, heap growth under 50 MB", async ({
  browser,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 48, autostart: false });
  const open = async (name: string, path: string) => {
    const page = await (await browser.newContext()).newPage();
    await page.addInitScript(observeLongTasks);
    if (name === "fan")
      await storeProfile(page, { mode: "casual", language: "vi", beginner: true });
    await page.goto(path);
    return page;
  };
  const pages = {
    studio: await open("studio", `/studio/?match=${matchId}`),
    fan: await open("fan", `/fan/?match=${matchId}`),
    overlay: await open("overlay", `/overlay/?match=${matchId}&lang=en`),
  };
  await expect(pages.studio.getByTestId("match-status")).toHaveAttribute("data-status", "created");
  await expect(pages.fan.getByTestId("match-status")).toHaveAttribute("data-status", "created");
  await expect(pages.overlay.getByTestId("overlay-root")).toHaveAttribute(
    "data-connection",
    "open",
  );
  const before = {
    studio: await heapMb(pages.studio),
    fan: await heapMb(pages.fan),
    overlay: await heapMb(pages.overlay),
  };

  await request.post(`/matches/${matchId}/start`);
  await waitStatus(request, matchId, "finished");
  await expect(pages.studio.getByTestId("match-status")).toHaveAttribute("data-status", "finished");
  await expect(pages.fan.getByTestId("match-status")).toHaveAttribute("data-status", "finished");
  await pages.studio.waitForTimeout(1000); // let the last frames render

  const report: Record<string, unknown> = {};
  for (const [name, page] of Object.entries(pages)) {
    const longTasks = await page.evaluate(() => window.longTasks ?? []);
    const growth = (await heapMb(page)) - before[name as keyof typeof before];
    const stats = await socketStats(page, name);
    report[name] = {
      longTasks: longTasks.length,
      worstLongTaskMs: Math.round(Math.max(0, ...longTasks)),
      heapGrowthMb: Number(growth.toFixed(1)),
      stats,
    };
    expect(Math.max(0, ...longTasks), `${name} long task`).toBeLessThan(200);
    expect(growth, `${name} heap growth`).toBeLessThan(50);
    expect(stats, `${name} socket`).toMatchObject({ gaps: 0, duplicates: 0, badFrames: 0 });
  }
  test.info().annotations.push({ type: "F7 1000x", description: JSON.stringify(report) });
  console.log("F7 1000x", JSON.stringify(report));
  for (const page of Object.values(pages)) await page.context().close();
});

test("F7 message → store latency p95 under 100 ms at 20x", async ({ page, request }) => {
  const matchId = await createMatch(request, { seed: 49, speed: 20 });
  await page.goto(`/studio/?match=${matchId}`);
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "running");
  const samples = () =>
    page.evaluate(() => window.punditDebug?.connections.studio?.latencies() ?? []);
  // about 6 messages a second at 20x
  await expect
    .poll(async () => (await samples()).length, { timeout: 60_000, intervals: [1000] })
    .toBeGreaterThanOrEqual(150);
  const sorted = [...(await samples())].sort((a, b) => a - b);
  const p95 = sorted[Math.floor(sorted.length * 0.95)]!;
  console.log(`F7 20x latency p95 ${p95.toFixed(1)} ms over ${sorted.length} messages`);
  expect(p95).toBeLessThan(100);
  await stopMatch(request, matchId);
});
