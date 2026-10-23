// F6 (docs/day4-design.md section 12): axe on the five pages (0 serious / critical) and a
// see-through overlay: outside the lower-third every sampled pixel has alpha 0.
import { expect, test } from "@playwright/test";
import {
  createMatch,
  expectSynthetic,
  openAdvanced,
  seriousA11y,
  storeProfile,
  waitStatus,
} from "./helpers";

test("F6 every page passes axe (WCAG 2 A/AA, no serious or critical violation)", async ({
  page,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 47 });
  await waitStatus(request, matchId, "finished");
  await storeProfile(page, { mode: "casual", language: "vi", beginner: true });
  const pages = [
    ["landing", "/", "scenario-dominant_loser"],
    ["studio", `/studio/?match=${matchId}`, "talking-point"],
    ["fan", `/fan/?match=${matchId}`, "commentary-card"],
    ["compare", `/compare/?match=${matchId}`, "compare-row"],
  ] as const;
  const found: Record<string, string[]> = {};
  for (const [name, path, ready] of pages) {
    await page.goto(path);
    await expectSynthetic(page);
    if (name === "landing") await openAdvanced(page); // axe reads the scenario cards too
    await expect(page.getByTestId(ready).first()).toBeVisible();
    found[name] = await seriousA11y(page);
  }
  await page.goto(`/overlay/?match=${matchId}&lang=en`);
  await expect(page.getByTestId("overlay-root")).toHaveAttribute("data-connection", "open");
  found.overlay = await seriousA11y(page);
  expect(found).toEqual({ landing: [], studio: [], fan: [], compare: [], overlay: [] });
});

test("F6 the overlay is transparent outside the lower-third", async ({ page, request }) => {
  const matchId = await createMatch(request, { seed: 42, speed: 60, from_minute: 24 });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`/overlay/?match=${matchId}&lang=en`);
  const lowerThird = page.getByTestId("lower-third");
  await expect(lowerThird).toBeVisible({ timeout: 45_000 });
  const box = (await lowerThird.boundingBox())!;
  const png = await page.screenshot({ omitBackground: true });
  const samples = await page.evaluate(
    async ({ base64, box }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${base64}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext("2d")!;
      context.drawImage(image, 0, 0);
      const alpha = (x: number, y: number) => context.getImageData(x, y, 1, 1).data[3];
      const inside = alpha(Math.round(box.x + box.width / 2), Math.round(box.y + box.height / 2));
      const outside: number[] = [];
      for (let x = 5; x < image.width; x += 97) {
        for (let y = 5; y < image.height; y += 89) {
          // the box plus its drop shadow
          const inBox =
            x >= box.x - 80 &&
            x <= box.x + box.width + 80 &&
            y >= box.y - 80 &&
            y <= box.y + box.height + 80;
          if (!inBox) outside.push(alpha(x, y)!);
        }
      }
      return { inside, outside };
    },
    { base64: png.toString("base64"), box },
  );
  expect(samples.inside).toBeGreaterThan(200); // the graphic itself is opaque
  expect(samples.outside.length).toBeGreaterThan(50);
  expect(new Set(samples.outside)).toEqual(new Set([0]));
  await request.post(`/matches/${matchId}/stop`);
});
