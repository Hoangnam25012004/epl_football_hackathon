// Network failures and refusals with no backend (docs/day4-design.md section 12 E11, section 11.3
// socket cases): the pages say what is happening, retry on their own and never go blank.
import { expect, test } from "@playwright/test";
import {
  analyst,
  casual,
  helloOf,
  lateHello,
  MATCH,
  mockRest,
  mockSocket,
  play,
  watchErrors,
} from "./mock";

test("E11 the backend is gone for 5 s: reconnect banner, then the studio catches up", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await mockRest(page);
  let downUntil = Infinity;
  await mockSocket(page, ({ ws, index }) => {
    if (index === 0) {
      play(ws, analyst.slice(0, 400));
      setTimeout(() => {
        downUntil = Date.now() + 5000;
        ws.close({ code: 1012, reason: "service restart" });
      }, 1500);
    } else if (Date.now() < downUntil) {
      ws.close({ code: 1011, reason: "backend down" });
    } else {
      play(ws, lateHello);
    }
  });
  await page.goto(`/studio/?match=${MATCH}`);
  await expect(page.getByTestId("event-row").first()).toBeVisible();
  const banner = page.getByTestId("connection-banner");
  await expect(banner).toHaveAttribute("data-status", "reconnecting");
  await expect(banner).toContainText(/Reconnecting in \d+s/);
  await expect(banner).toHaveCount(0, { timeout: 15_000 });
  // caught up with the state of the late hello (58', 0–1)
  const snapshot = helloOf(lateHello).payload.snapshot;
  await expect(page.getByTestId("clock")).toHaveText(snapshot.clock_display);
  await expect(page.getByTestId("score")).toHaveText("0–1");
  expect(errors).toEqual([]);
});

test("4404: the studio offers to create the match, then goes live", async ({ page }) => {
  const errors = watchErrors(page);
  let created = false;
  await mockRest(page, { onCreate: () => (created = true) });
  await mockSocket(page, ({ ws }) => {
    if (!created) ws.close({ code: 4404, reason: "unknown match" });
    else play(ws, analyst.slice(0, 60));
  });
  await page.goto(`/studio/?match=${MATCH}`);
  const banner = page.getByTestId("connection-banner");
  await expect(banner).toContainText("This match has not been created yet.");
  await expect(page.getByTestId("synthetic-badge").first()).toBeVisible();
  await banner.getByRole("button", { name: "Create this match" }).click();
  await expect(banner).toHaveCount(0);
  await expect(page.getByTestId("scoreboard")).toContainText("HAR");
  expect(created).toBe(true);
  expect(errors).toEqual([]);
});

test("4404: the overlay waits without showing anything, then goes on air", async ({ page }) => {
  const errors = watchErrors(page);
  await mockRest(page);
  let exists = false;
  await mockSocket(page, ({ ws }) => {
    if (!exists) ws.close({ code: 4404, reason: "unknown match" });
    else play(ws, analyst);
  });
  await page.goto(`/overlay/?match=${MATCH}&lang=en`);
  const root = page.getByTestId("overlay-root");
  await expect(root).toHaveAttribute("data-connection", "waiting");
  expect((await root.innerText()).trim()).toBe(""); // nothing on the broadcast picture
  exists = true;
  await expect(root).toHaveAttribute("data-connection", "open", { timeout: 10_000 });
  await expect(page.getByTestId("lower-third")).toBeVisible({ timeout: 10_000 });
  expect(errors).toEqual([]);
});

test("4422: the fan chooses a profile again, told why, and the new one connects", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await mockRest(page);
  const queries: string[] = [];
  await mockSocket(page, ({ ws, query, index }) => {
    queries.push(query.toString());
    if (index === 0) ws.close({ code: 4422, reason: "invalid profile" });
    else play(ws, query.get("lang") === "vi" ? casual.slice(0, 300) : analyst.slice(0, 300));
  });
  await page.addInitScript(() =>
    window.localStorage.setItem(
      "pundit.profile",
      JSON.stringify({
        mode: "casual",
        language: "vi",
        team: "harbor_lions",
        player: null,
        beginner: true,
        metric: null,
      }),
    ),
  );
  await page.goto(`/fan/?match=${MATCH}`);
  await expect(page.getByTestId("connection-banner")).toContainText(
    "Hồ sơ người xem không hợp lệ với trận này.",
  );
  await expect(page.getByTestId("profile-setup")).toBeVisible();
  await page.getByTestId("mode-analyst").click();
  await page.getByRole("radio", { name: "English" }).click();
  await page.getByTestId("profile-start").click();
  await expect(page.getByTestId("commentary-card").first()).toBeVisible();
  await expect(page.getByTestId("connection-banner")).toHaveCount(0);
  expect(queries).toHaveLength(2); // a refused profile is never retried as is
  expect(queries[1]).toContain("mode=analyst");
  expect(errors).toEqual([]);
});

test("4429: too many viewers, the page says so and retries after 10 s", async ({ page }) => {
  const errors = watchErrors(page);
  await mockRest(page);
  let attempts = 0;
  await mockSocket(page, ({ ws, index }) => {
    attempts = index + 1;
    if (index === 0) ws.close({ code: 4429, reason: "too many viewers" });
    else play(ws, analyst.slice(0, 60));
  });
  await page.goto(`/studio/?match=${MATCH}`);
  const banner = page.getByTestId("connection-banner");
  await expect(banner).toContainText("This match has too many viewers. Retrying…");
  await page.waitForTimeout(5000);
  expect(attempts).toBe(1); // no hammering before the 10 s are up
  await expect(banner).toHaveCount(0, { timeout: 10_000 });
  expect(attempts).toBe(2);
  expect(errors).toEqual([]);
});

test("every page renders without a match, and with the backend down", async ({ page }) => {
  const errors = watchErrors(page);
  await mockRest(page);
  for (const path of ["/studio/", "/fan/", "/compare/"]) {
    await page.goto(path);
    await expect(
      page.getByRole("link", { name: /Back to matches|Về trang chọn trận/ }),
    ).toBeVisible();
    await expect(page.getByTestId("synthetic-badge").first()).toBeVisible();
  }
  // backend down: every API call fails, every socket closes at once
  await page.unrouteAll({ behavior: "wait" });
  await page.route(
    (url) => /^\/(healthz|scenarios|matches|claims)(\/|$)/.test(url.pathname),
    (route) => route.abort("connectionrefused"),
  );
  await mockSocket(page, ({ ws }) => ws.close({ code: 1011, reason: "backend down" }));
  for (const path of [`/studio/?match=${MATCH}`, `/compare/?match=${MATCH}`, "/"]) {
    await page.goto(path);
    await expect(page.getByTestId("synthetic-badge").first()).toBeVisible();
    await expect(page.locator("body")).not.toBeEmpty();
  }
  await page.goto(`/studio/?match=${MATCH}`);
  await expect(page.getByTestId("connection-banner")).toHaveAttribute(
    "data-status",
    "reconnecting",
  );
  // a refused fetch is logged by the browser; nothing else may be
  expect(errors.filter((error) => !error.includes("net::ERR_CONNECTION_REFUSED"))).toEqual([]);
});
