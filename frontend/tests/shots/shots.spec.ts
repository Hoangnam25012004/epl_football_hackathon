// Day 6 screenshot set (docs/day6-design.md section 8.3), not part of the default run:
//   SHOTS=1 npx playwright test --project=shots
// Every page at 1920×1080, 1366×768 and 390×844, light and dark, VI and EN, with the recorded
// streams of the `mock` project (deterministic). Writes test-results/shots/<page>-<width>-
// <theme>-<lang>.png and an index.html that lays them out in a table, for a look by eye. At
// 1366×768 (a judge's laptop) every page × theme × language also passes axe (W13).
// The momentum timeline and the match stats are answered as the real backend answers them at
// full time (tests/fixtures/rest, same match and seed as the streams), and a page is shot once
// the event feed names the players: the shots show what a viewer sees.
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { seriousA11y } from "../e2e/helpers";
import {
  analyst,
  analystVi,
  casual,
  MATCH,
  mockRest,
  mockSocket,
  play,
  watchErrors,
} from "../e2e-mock/mock";

const OUT = join(__dirname, "..", "..", "test-results", "shots");
const REST = join(__dirname, "..", "fixtures", "rest");
const recorded = (name: string): unknown => JSON.parse(readFileSync(join(REST, name), "utf8"));
const SIZES = [
  { width: 1920, height: 1080 },
  { width: 1366, height: 768 },
  { width: 390, height: 844 },
] as const;
const THEMES = ["light", "dark"] as const;
const LANGS = ["vi", "en"] as const;
const HEALTH = {
  status: "ok",
  llm_provider: "mock",
  model: "mock",
  degraded: [],
  sessions: 1,
  synthetic: true,
  speech: { provider: "mock" },
  mcp: { enabled: true, status: "ok", path: "/mcp", transport: "streamable-http" },
};

type Lang = (typeof LANGS)[number];
type Shot = { path: string; ready: string; full: boolean };

const PAGES: Record<string, (lang: Lang) => Shot> = {
  landing: () => ({ path: "/", ready: "demo-steps", full: true }),
  studio: () => ({ path: `/studio/?match=${MATCH}`, ready: "talking-point", full: false }),
  overlay: (lang) => ({
    path: `/overlay/?match=${MATCH}&lang=${lang}&debug=1`,
    ready: "lower-third",
    full: false,
  }),
  fan: () => ({ path: `/fan/?match=${MATCH}`, ready: "commentary-card", full: true }),
  compare: () => ({ path: `/compare/?match=${MATCH}`, ready: "compare-row", full: true }),
};

async function prepare(page: Page, name: string, lang: Lang): Promise<void> {
  await page.addInitScript(
    ([ui, profile]) => {
      window.localStorage.setItem("pundit.ui-lang", ui);
      window.localStorage.setItem("pundit.profile", profile);
    },
    [
      lang,
      JSON.stringify({
        mode: lang === "vi" ? "casual" : "analyst",
        language: lang,
        team: lang === "vi" ? "harbor_lions" : null,
        player: null,
        beginner: lang === "vi",
        metric: null,
        voice: null,
      }),
    ] as const,
  );
  // the studio's preview replays the lower-third queue, a card every few seconds: with every
  // animation at its end state, axe and the screenshot never catch a card half faded in
  await page.addInitScript(() => {
    const style = document.createElement("style");
    style.textContent =
      "*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;" +
      "transition-duration:0s!important;transition-delay:0s!important}";
    document.addEventListener("DOMContentLoaded", () => document.head.append(style));
  });
  await mockRest(page);
  // later routes run first: these answer before mockRest's empty timeline and stats
  await page.route("**/healthz", (route) => route.fulfill({ json: HEALTH }));
  await page.route(
    (url) => url.pathname === `/matches/${MATCH}/timeline`,
    (route) => route.fulfill({ json: recorded(`timeline.${MATCH}.json`) }),
  );
  await page.route(
    (url) => url.pathname === `/matches/${MATCH}/stats`,
    (route) => {
      const period = new URL(route.request().url()).searchParams.get("period") ?? "FULL";
      return route.fulfill({ json: recorded(`stats.${MATCH}.${period}.json`) });
    },
  );
  await mockSocket(page, ({ ws, query }) => {
    // the casual VI stream for a casual viewer (fan VI, the compare page's right side); the
    // studio and the overlay connect as an analyst in their own language
    if (query.get("mode") === "casual") play(ws, casual);
    else play(ws, query.get("lang") === "vi" ? analystVi : analyst);
  });
  if (name === "landing") {
    await page.addInitScript(() => {
      // one of "my matches", so the list shows
      window.localStorage.setItem(
        "pundit.my-sessions",
        JSON.stringify([{ id: "dominant_loser-42", created: 1 }]),
      );
    });
  }
}

for (const [name, shotOf] of Object.entries(PAGES)) {
  for (const size of SIZES) {
    for (const theme of THEMES) {
      for (const lang of LANGS) {
        const file = `${name}-${size.width}-${theme}-${lang}.png`;
        test(file, async ({ page }) => {
          const errors = watchErrors(page);
          await page.setViewportSize(size);
          await page.emulateMedia({ colorScheme: theme });
          await prepare(page, name, lang);
          const shot = shotOf(lang);
          await page.goto(shot.path);
          await expect(page.getByTestId(shot.ready).first()).toBeVisible();
          if (name === "studio") {
            // the squads and the panels have loaded: names, not player ids; a chart; stats rows
            await expect(page.getByTestId("event-feed")).not.toContainText(/[a-z]+_[a-z]+-\d/);
            await expect(page.getByTestId("momentum-timeline")).toBeVisible();
            await expect(page.getByTestId("stats-row").first()).toBeVisible();
          }
          await page.waitForFunction(() =>
            document.getAnimations().every((animation) => animation.playState !== "running"),
          );
          mkdirSync(OUT, { recursive: true });
          await page.screenshot({ path: join(OUT, file), fullPage: shot.full });
          const overflow = await page.evaluate(
            () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
          );
          expect(overflow, "no sideways scroll").toBeLessThanOrEqual(0);
          if (size.width === 1366) expect(await seriousA11y(page), "axe").toEqual([]);
          if (name === "studio") {
            // no panel over another (one column below 1280 px: they overlapped before Day 6)
            const boxes = await page
              .locator("[data-testid^=panel-]")
              .evaluateAll((panels) =>
                panels.map((panel) => panel.getBoundingClientRect().toJSON() as DOMRect),
              );
            for (const [i, a] of boxes.entries()) {
              for (const b of boxes.slice(i + 1)) {
                const apart =
                  a.bottom <= b.top + 1 ||
                  b.bottom <= a.top + 1 ||
                  a.right <= b.left + 1 ||
                  b.right <= a.left + 1;
                expect(apart, "panels overlap").toBe(true);
              }
            }
            if (size.width < 1280) {
              await page.locator("main").evaluate((main) => main.scrollTo(0, main.scrollHeight));
              await page.screenshot({ path: join(OUT, file.replace(".png", "-end.png")) });
            }
          }
          expect(errors).toEqual([]);
        });
      }
    }
  }
}

test.afterAll(() => {
  const files = readdirSync(OUT).filter((f) => f.endsWith(".png"));
  const rows = Object.keys(PAGES).flatMap((name) =>
    SIZES.map((size) => {
      const cells = THEMES.flatMap((theme) =>
        LANGS.map((lang) => {
          const file = `${name}-${size.width}-${theme}-${lang}.png`;
          return files.includes(file)
            ? `<td><a href="${file}"><img src="${file}" alt="${file}"></a></td>`
            : "<td>missing</td>";
        }),
      );
      return `<tr><th>${name}<br>${size.width}×${size.height}</th>${cells.join("")}</tr>`;
    }),
  );
  const head = THEMES.flatMap((t) => LANGS.map((l) => `<th>${t} · ${l}</th>`)).join("");
  writeFileSync(
    join(OUT, "index.html"),
    `<!doctype html><meta charset="utf-8"><title>Pundit Copilot screenshots (synthetic data)</title>
<style>body{font-family:sans-serif}td,th{border:1px solid #ccc;padding:4px;vertical-align:top}
img{width:300px}</style><h1>Screenshots · synthetic data</h1>
<table><tr><th></th>${head}</tr>${rows.join("")}</table>`,
  );
});
