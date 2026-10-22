import AxeBuilder from "@axe-core/playwright";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import type { ClaimResult } from "@/lib/contracts";
import type { ViewerProfile } from "@/lib/profile";

export type Status = "created" | "running" | "paused" | "finished" | "stopped" | "error";

/** POST /matches with fast, restarted defaults; returns the match id. */
export async function createMatch(
  request: APIRequestContext,
  body: Record<string, unknown>,
): Promise<string> {
  const response = await request.post("/matches", {
    data: {
      scenario: "dominant_loser",
      seed: 42,
      speed: 1000,
      autostart: true,
      restart: true,
      ...body,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { match_id: string }).match_id;
}

export async function matchInfo(
  request: APIRequestContext,
  matchId: string,
): Promise<{ status: Status; speed: number; events_applied: number }> {
  return (await (await request.get(`/matches/${matchId}`)).json()).info;
}

export async function waitStatus(
  request: APIRequestContext,
  matchId: string,
  ...statuses: Status[]
): Promise<void> {
  await expect
    .poll(async () => (await matchInfo(request, matchId)).status, {
      timeout: 60_000,
      intervals: [200, 500, 1000],
    })
    .toMatch(new RegExp(`^(${statuses.join("|")})$`));
}

export async function stopMatch(request: APIRequestContext, matchId: string): Promise<void> {
  await request.post(`/matches/${matchId}/stop`);
}

/** A saved fan profile, so the fan page connects without the setup screen. */
export async function storeProfile(page: Page, profile: Partial<ViewerProfile>): Promise<void> {
  const full: ViewerProfile = {
    mode: "casual",
    language: "vi",
    team: null,
    player: null,
    beginner: false,
    metric: null,
    ...profile,
  };
  await page.addInitScript((value) => {
    window.localStorage.setItem("pundit.profile", value);
  }, JSON.stringify(full));
}

/** The socket counters of one page connection (`window.punditDebug`). */
export async function socketStats(page: Page, name: string) {
  return page.evaluate((key) => window.punditDebug?.connections[key]?.stats() ?? null, name);
}

/** Submit a claim in the studio and return the server's result. */
export async function submitClaim(page: Page, text: string): Promise<ClaimResult> {
  const response = page.waitForResponse(
    (r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/claims/verify",
  );
  await page.getByTestId("claim-input").fill(text);
  await page.getByTestId("claim-submit").click();
  const result = await response;
  expect(result.ok(), await result.text()).toBeTruthy();
  return (await result.json()) as ClaimResult;
}

export async function expectSynthetic(page: Page): Promise<void> {
  await expect(page.getByTestId("synthetic-badge").first()).toBeVisible();
}

/** Console errors and uncaught page errors of a page. */
export function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  return errors;
}

/** Serious or critical WCAG 2 A/AA violations, measured once the entry animations (fade-ins
 * that lower the contrast while they run) have finished. */
export async function seriousA11y(page: Page): Promise<string[]> {
  await page.waitForFunction(() =>
    document.getAnimations().every((animation) => animation.playState !== "running"),
  );
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.length} × ${v.nodes[0]?.target.join(" ")} (${v.help})`);
}
