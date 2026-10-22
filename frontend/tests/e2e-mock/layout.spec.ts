// Layout with recorded streams (docs/day4-design.md section 12 F6 and M4): the long model texts of
// fixture B never push a page sideways, and the reference screenshots (1920×1080 studio, overlay,
// compare; 390×844 fan) are written to test-results for a look by eye.
import { expect, test } from "@playwright/test";
import {
  analyst,
  casual,
  expectNoHorizontalOverflow,
  longAnalyst,
  longCasual,
  MATCH,
  mockRest,
  mockSocket,
  play,
  watchErrors,
} from "./mock";

const FULL_HD = { width: 1920, height: 1080 };

test("studio at 1920×1080 with fixture B: long texts stay inside their panels", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await page.setViewportSize(FULL_HD);
  await mockRest(page);
  await mockSocket(page, ({ ws }) => play(ws, longAnalyst));
  await page.goto(`/studio/?match=${MATCH}`);
  await expect(page.getByTestId("talking-point").first()).toBeVisible();
  await expect(page.getByTestId("lower-third-item").first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
  const clipped = await page
    .getByTestId("talking-point")
    .evaluateAll((points) => points.filter((p) => p.scrollWidth > p.clientWidth + 1).length);
  expect(clipped).toBe(0);
  await page.screenshot({ path: test.info().outputPath("studio-1920x1080.png") });
  expect(errors).toEqual([]);
});

test("overlay at 1920×1080 (debug): one lower-third in the safe area, the rest see-through", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await page.setViewportSize(FULL_HD);
  await mockRest(page);
  await mockSocket(page, ({ ws }) => play(ws, analyst));
  await page.goto(`/overlay/?match=${MATCH}&lang=en&debug=1`);
  const lowerThird = page.getByTestId("lower-third");
  await expect(lowerThird).toBeVisible();
  await expect(lowerThird).toHaveCount(1);
  // measured at rest, after the entry animation
  await lowerThird.evaluate((element) =>
    Promise.all(element.getAnimations().map((animation) => animation.finished)),
  );
  const box = (await lowerThird.boundingBox())!;
  // inside the 5 % safe area of a 1920×1080 picture
  expect(box.x).toBeGreaterThanOrEqual(96);
  expect(box.y + box.height).toBeLessThanOrEqual(1080 - 54 + 1);
  expect(box.x + box.width).toBeLessThanOrEqual(1920 - 96);
  await page.screenshot({ path: test.info().outputPath("overlay-1920x1080-debug.png") });
  expect(errors).toEqual([]);
});

test("fan at 390×844 with fixture B: cards wrap, nothing scrolls sideways", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await mockRest(page);
  await mockSocket(page, ({ ws }) => play(ws, longCasual));
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
  await expect(page.getByTestId("commentary-card").first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: test.info().outputPath("fan-390x844.png") });
  expect(errors).toEqual([]);
});

test("compare at 1920×1080: two profiles, rows aligned by moment", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize(FULL_HD);
  await mockRest(page);
  await mockSocket(page, ({ ws, query }) =>
    play(ws, query.get("lang") === "vi" ? casual : analyst),
  );
  await page.goto(`/compare/?match=${MATCH}`);
  const row = page.getByTestId("compare-row").first();
  await expect(row.getByTestId("commentary-card")).toHaveCount(2);
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: test.info().outputPath("compare-1920x1080.png") });
  expect(errors).toEqual([]);
});
