// Day 5 with no backend (docs/day5-design.md section 13, V7): what needs exact timing. A2 a goal
// cuts in front of a quiet line read for over a second; A9 the connection drops while a line is
// read: no crash, a banner, and after the new hello no line is read twice.
import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";
import type { AnyWsMessage } from "@/lib/ws-types";
import { fixture } from "../fixtures";
import { audioState } from "../voice";
import { casual, clipMs, helloOf, MATCH, mockRest, mockSocket, play, watchErrors } from "./mock";

const female = fixture("dominant_loser-42.casual-vi-beginner-har-female");
const femaleHello = helloOf(female);
type Commentary = Extract<AnyWsMessage, { type: "commentary" }>;
const spoken = female.filter(
  (m): m is Commentary => m.type === "commentary" && Boolean(m.payload.speech?.audio),
);

function line(kind: string, excitement: number): Commentary {
  const found = spoken.find(
    (m) => m.payload.kind === kind && (m.payload.excitement ?? 0) === excitement,
  );
  if (!found) throw new Error(`no spoken ${kind} in the fixture`);
  return found;
}

/** The message as the n-th after a hello (the socket drops anything out of order). */
const as = (message: AnyWsMessage, n: number): AnyWsMessage => ({ ...message, n });

async function storeVoiceProfile(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "pundit.profile",
      JSON.stringify({
        mode: "casual",
        language: "vi",
        team: "harbor_lions",
        player: null,
        beginner: true,
        metric: null,
        voice: "female",
      }),
    );
  });
}

/** The fan page with the voice on; resolves with the socket that asked for the voice. */
async function fanWithVoice(
  page: Page,
  onVoice: (ws: WebSocketRoute, index: number) => void,
): Promise<void> {
  await storeVoiceProfile(page);
  await mockSocket(page, ({ ws, query, index }) => {
    if (query.get("voice") === "female") onVoice(ws, index);
    else play(ws, [helloOf(casual)]);
  });
  await page.goto(`/fan/?match=${MATCH}`);
  await page.getByTestId("voice-toggle").click();
  await expect.poll(async () => (await audioState(page))?.played ?? []).toContain("greeting-1");
}

const speaking = async (page: Page) => (await audioState(page))?.speakingId ?? null;

test("A2 a goal cuts in front of a quiet line read for over a second", async ({ page }) => {
  const errors = watchErrors(page);
  await mockRest(page);
  const quiet = line("dangerous_spell", 1);
  const goal = line("goal", 3);
  clipMs.set(quiet.payload.speech?.audio?.url ?? "", 6000);
  clipMs.set(goal.payload.speech?.audio?.url ?? "", 800);
  let socket: WebSocketRoute | null = null;
  await fanWithVoice(page, (ws) => {
    socket = ws;
    play(ws, [femaleHello]);
  });
  const ws = socket as WebSocketRoute | null;
  if (!ws) throw new Error("no socket with the voice");
  play(ws, [as(quiet, 1)]);
  await expect.poll(() => speaking(page)).toBe(quiet.payload.id);
  await page.waitForTimeout(1300); // read for more than PREEMPT_AFTER_MS
  play(ws, [as(goal, 2)]);
  await expect.poll(() => speaking(page)).toBe(goal.payload.id);
  const state = await audioState(page);
  expect(state?.counts.preempted).toBe(1);
  expect(state?.maxConcurrent).toBe(1);
  await expect(
    page.locator(`[data-testid=commentary-card][data-id="${goal.payload.id}"]`),
  ).toHaveAttribute("data-speaking", "true");
  await expect.poll(async () => (await audioState(page))?.played ?? []).toContain(goal.payload.id);
  expect((await audioState(page))?.played).not.toContain(quiet.payload.id); // cut, not finished
  expect(errors).toEqual([]);
});

test("A9 the connection drops while a line is read: banner, no crash, nothing read twice", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await mockRest(page);
  const first = line("period_start", 0);
  const later = line("big_chance", 2);
  clipMs.set(first.payload.speech?.audio?.url ?? "", 3000);
  clipMs.set(later.payload.speech?.audio?.url ?? "", 500);
  let downUntil = 0;
  const sockets: WebSocketRoute[] = [];
  await fanWithVoice(page, (ws) => {
    sockets.push(ws);
    if (sockets.length === 1) play(ws, [femaleHello]);
    else if (Date.now() < downUntil) ws.close({ code: 1011, reason: "backend down" });
    // the new hello knows the first line; the server sends it again, then a new one
    else play(ws, [{ ...femaleHello, n: 40 }, as(first, 41), as(later, 42)]);
  });
  const ws = sockets[0];
  if (!ws) throw new Error("no socket with the voice");
  play(ws, [as(first, 1)]);
  await expect.poll(() => speaking(page)).toBe(first.payload.id);
  downUntil = Date.now() + 5000;
  await ws.close({ code: 1012, reason: "service restart" });
  const banner = page.getByTestId("connection-banner");
  await expect(banner).toHaveAttribute("data-status", "reconnecting");
  await expect(banner).toHaveCount(0, { timeout: 20_000 });
  await expect
    .poll(async () => (await audioState(page))?.played ?? [], { timeout: 15_000 })
    .toContain(later.payload.id);
  const played = (await audioState(page))?.played ?? [];
  expect(played.filter((id) => id === first.payload.id).length).toBeLessThanOrEqual(1);
  expect(new Set(played).size).toBe(played.length);
  expect((await audioState(page))?.maxConcurrent).toBe(1);
  expect(errors).toEqual([]);
});
