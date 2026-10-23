// Day 6 private sessions on the real backend (docs/day6-design.md section 6, W11): two viewers
// each start their own demo match and never disturb each other; a restart keeps the session's
// id; a link to a private session the server no longer has makes it again under the same id.
import { expect, test, type Page } from "@playwright/test";
import { matchInfo, stopMatch, waitStatus, watchErrors } from "./helpers";

const PRIVATE = /\/studio\/\?match=(dominant_loser-42~[a-z0-9]{8})$/;

async function startMyDemo(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByTestId("start-my-demo").click();
  await page.waitForURL(PRIVATE);
  const id = PRIVATE.exec(page.url())?.[1];
  expect(id).toBeTruthy();
  await expect(page.getByTestId("private-chip")).toBeVisible();
  return id as string;
}

test("P1 two viewers, two private sessions: one pauses, the other plays on", async ({
  browser,
  request,
}) => {
  const first = await (await browser.newContext()).newPage();
  const second = await (await browser.newContext()).newPage();
  const errors = [...watchErrors(first), ...watchErrors(second)];
  const one = await startMyDemo(first);
  const two = await startMyDemo(second);
  expect(one).not.toBe(two);

  await first.getByTestId("play-pause").click();
  await second.getByTestId("play-pause").click();
  await waitStatus(request, one, "running");
  await waitStatus(request, two, "running");
  await first.getByTestId("play-pause").click();
  await waitStatus(request, one, "paused");
  expect((await matchInfo(request, two)).status).toBe("running");

  const shared = ((await (await request.get("/matches")).json()) as { match_id: string }[]).map(
    (m) => m.match_id,
  );
  expect(shared).not.toContain(one);
  expect(shared).not.toContain(two);
  await first.goto("/");
  await expect(first.getByTestId("my-sessions")).toContainText(one);
  await expect(first.getByTestId("my-sessions")).not.toContainText(two);
  expect(errors).toEqual([]);
  await stopMatch(request, one);
  await stopMatch(request, two);
});

test("P2 restarting from a minute keeps the private session's id", async ({ page, request }) => {
  const errors = watchErrors(page);
  const id = await startMyDemo(page);
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByTestId("restart-from").selectOption("45");
  await waitStatus(request, id, "running", "finished");
  expect(page.url()).toContain(id);
  const info = (await (await request.get(`/matches/${id}`)).json()).info;
  expect(info.match_id).toBe(id);
  expect(info.private).toBe(true);
  expect(info.from_minute).toBe(45);
  expect(errors).toEqual([]);
  await stopMatch(request, id);
});

test("P3 a private link the server no longer has: the banner makes it again, same id", async ({
  page,
  request,
}) => {
  const id = "dominant_loser-42~e2eagain";
  expect((await request.get(`/matches/${id}`)).status()).toBe(404);
  await page.goto(`/studio/?match=${id}`);
  const banner = page.getByTestId("connection-banner");
  await expect(banner).toHaveAttribute("data-status", "fatal");
  await banner.getByRole("button").click();
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "created");
  const info = (await (await request.get(`/matches/${id}`)).json()).info;
  expect(info.private).toBe(true);
  expect(info.match_id).toBe(id);
  await expect(page.getByTestId("private-chip")).toBeVisible();
  await stopMatch(request, id);
});
