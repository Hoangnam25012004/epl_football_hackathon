// Day 5 E2E on the real backend with the mock voice (TTS_PROVIDER=mock, MOCK_TTS_TIME_SCALE=0.1):
// docs/day5-design.md section 13, V7. A2 (a goal cuts in) and A9 (the connection drops while
// reading) need exact timing, so they run in the `mock` project (tests/e2e-mock/voice.spec.ts).
import { expect, test, type Page } from "@playwright/test";
import {
  createMatch,
  seriousA11y,
  socketStats,
  stopMatch,
  storeProfile,
  watchErrors,
} from "./helpers";
import { audioState, installFakeSpeech, spokenByBrowser, unexpected, watchSpeech } from "../voice";

const FAN = { mode: "casual", language: "vi", team: "harbor_lions", beginner: true } as const;
const CLIP = /\/audio\/[0-9a-f]{32}\.(wav|mp3)$/;

/** Open the fan player with a saved profile and turn the voice on (the click unlocks audio). */
async function listen(page: Page, matchId: string): Promise<void> {
  await storeProfile(page, FAN);
  await page.goto(`/fan/?match=${matchId}`);
  await expect(page.getByTestId("match-status")).toHaveAttribute("data-status", "running");
  await page.getByTestId("voice-toggle").click();
  await expect(page.getByTestId("voice-toggle")).toHaveAttribute("aria-pressed", "true");
}

const played = async (page: Page) => (await audioState(page))?.played ?? [];
const livePlayed = async (page: Page) =>
  (await played(page)).filter((id) => !id.startsWith("greeting-"));

test("A1 the voice: the greeting first, then live lines with speech, one at a time, in order", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const speech = watchSpeech(page);
  const matchId = await createMatch(request, { seed: 51, speed: 20 });
  await listen(page, matchId);
  await expect(page.getByTestId("voice-status")).toHaveText("Giọng thử nghiệm");
  await expect(page.getByText("Giọng đọc do AI tạo, đọc dữ liệu tổng hợp.")).toBeVisible();
  await expect.poll(() => played(page), { timeout: 30_000 }).toContain("greeting-1");
  await expect
    .poll(async () => (await livePlayed(page)).length, { timeout: 90_000 })
    .toBeGreaterThanOrEqual(2);

  const state = await audioState(page);
  expect(state?.played[0]).toBe("greeting-1");
  expect(state?.maxConcurrent).toBe(1);
  const live = await livePlayed(page);
  for (const id of live) expect(speech.lines.has(id), id).toBe(true); // read = sent with speech
  const quiet = live
    .map((id) => speech.lines.get(id))
    .filter((line) => line && line.excitement < 3)
    .map((line) => line?.n ?? 0);
  expect(quiet).toEqual([...quiet].sort((a, b) => a - b));
  expect([...speech.lines.values()].every((line) => line.voice === "vi-VN-HoaiMyNeural")).toBe(
    true,
  );
  // the card being read is its caption
  await expect(page.locator("[data-testid=commentary-card][data-speaking=true]")).toHaveCount(1, {
    timeout: 60_000,
  });
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("A3 paused in the studio: the line ends and no other starts; resumed, it reads on", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const matchId = await createMatch(request, { seed: 52, speed: 20 });
  await listen(page, matchId);
  await expect
    .poll(async () => (await livePlayed(page)).length, { timeout: 90_000 })
    .toBeGreaterThanOrEqual(1);
  expect((await request.post(`/matches/${matchId}/pause`)).ok()).toBeTruthy();
  await expect.poll(async () => (await audioState(page))?.paused).toBe(true);
  await expect.poll(async () => (await audioState(page))?.speakingId ?? null).toBeNull();
  const before = await played(page);
  await page.waitForTimeout(3000);
  expect(await played(page)).toEqual(before);
  expect((await audioState(page))?.speakingId ?? null).toBeNull();
  expect((await request.post(`/matches/${matchId}/resume`)).ok()).toBeTruthy();
  await expect.poll(async () => (await audioState(page))?.paused).toBe(false);
  await expect
    .poll(async () => (await played(page)).length, { timeout: 90_000 })
    .toBeGreaterThan(before.length);
  expect((await audioState(page))?.maxConcurrent).toBe(1);
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("A4 the male voice: a new connection, other clips, NamMinh, served by /audio", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const speech = watchSpeech(page);
  const matchId = await createMatch(request, { seed: 53, speed: 20 });
  await listen(page, matchId);
  await expect.poll(() => speech.lines.size, { timeout: 90_000 }).toBeGreaterThan(0);
  const sockets = speech.sockets.length;
  await page.getByTestId("voice-male").click();
  await expect(page.getByTestId("voice-male")).toHaveAttribute("aria-checked", "true");
  await expect.poll(() => speech.sockets.length).toBeGreaterThan(sockets);
  expect(speech.sockets.at(-1)).toContain("voice=male");
  const male = () => [...speech.lines.values()].filter((l) => l.voice === "vi-VN-NamMinhNeural");
  await expect.poll(() => male().length, { timeout: 90_000 }).toBeGreaterThan(0);
  const female = [...speech.lines.values()].filter((l) => l.voice === "vi-VN-HoaiMyNeural");
  expect(female.length).toBeGreaterThan(0);
  const femaleUrls = new Set(female.map((l) => l.url));
  for (const line of male()) {
    expect(line.url).toMatch(CLIP);
    expect(femaleUrls.has(line.url)).toBe(false);
    const clip = await request.get(line.url ?? "");
    expect(clip.status()).toBe(200);
    expect(clip.headers()["content-type"]).toContain("audio/");
  }
  await expect.poll(() => played(page), { timeout: 30_000 }).toContain("greeting-2"); // in NamMinh
  await expect
    .poll(async () => (await played(page)).some((id) => male().some((l) => l.id === id)), {
      timeout: 90_000,
    })
    .toBe(true);
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("A5 the server's clips fail: the browser's voice reads the same words, then alone", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  await installFakeSpeech(page);
  await page.route(CLIP, (route) =>
    route.fulfill({
      status: 503,
      headers: { "Retry-After": "2", "Cache-Control": "no-store" },
      contentType: "application/json",
      body: JSON.stringify({ detail: "audio_unavailable", reason: "error" }),
    }),
  );
  const matchId = await createMatch(request, { seed: 54, speed: 20 });
  await listen(page, matchId);
  await expect
    .poll(async () => (await audioState(page))?.counts.fallbacks ?? 0, { timeout: 90_000 })
    .toBeGreaterThanOrEqual(3);
  await expect.poll(async () => (await audioState(page))?.browserOnly).toBe(true);
  await expect(page.getByTestId("voice-status")).toHaveText("Giọng dự phòng của trình duyệt");
  const spoken = await spokenByBrowser(page);
  expect(spoken.length).toBeGreaterThanOrEqual(3);
  for (const line of spoken) {
    expect(line.text).not.toMatch(/\d/); // the server's spoken words: no digit left
    expect(line.voice).toContain("HoaiMy");
  }
  expect((await audioState(page))?.maxConcurrent).toBe(1);
  expect(unexpected(errors, [/Failed to load resource.*503/])).toEqual([]);
  await stopMatch(request, matchId);
});

test("A6 the half-time recap: listen, seek (206), listen again; the live voice waits", async ({
  page,
  request,
}) => {
  const errors = watchErrors(page);
  const statuses: number[] = [];
  page.on("response", (response) => {
    if (CLIP.test(new URL(response.url()).pathname)) statuses.push(response.status());
  });
  const matchId = await createMatch(request, { seed: 55, speed: 20, from_minute: 41 });
  await listen(page, matchId);
  await page.getByTestId("tab-recap").click();
  await expect(page.getByTestId("recap-listen")).toBeVisible({ timeout: 90_000 });
  // the clip is made in the background when the recap is published: wait for the exact file
  const url = `/matches/${matchId}/recap?scope=H1&lang=vi&mode=casual&voice=female`;
  await expect
    .poll(async () => (await (await request.get(url)).json()).speech?.audio?.exact, {
      timeout: 30_000,
    })
    .toBe(true);
  await page.getByTestId("recap-listen").click();
  const player = page.getByTestId("recap-player");
  await expect(player).toBeVisible();
  await expect.poll(() => player.evaluate((el: HTMLAudioElement) => !el.paused)).toBe(true);
  await expect.poll(async () => (await audioState(page))?.recap).toBe(true);
  expect((await audioState(page))?.speakingId ?? null).toBeNull(); // no live line meanwhile

  const durationMs = Number(await player.getAttribute("data-duration-ms"));
  const real = await player.evaluate((el: HTMLAudioElement) => el.duration * 1000);
  expect(Math.abs(real - durationMs)).toBeLessThanOrEqual(1000);
  await player.evaluate(async (el: HTMLAudioElement) => {
    await new Promise<void>((resolve) => {
      el.addEventListener("seeked", () => resolve(), { once: true });
      el.currentTime = el.duration / 2;
    });
  });
  await page.getByTestId("recap-replay").click();
  await expect.poll(() => player.evaluate((el: HTMLAudioElement) => el.currentTime < 1)).toBe(true);
  await expect.poll(() => player.evaluate((el: HTMLAudioElement) => !el.paused)).toBe(true);
  expect(statuses).toContain(206);
  expect(statuses.every((status) => status === 200 || status === 206)).toBe(true);
  // when it ends the live queue goes on
  await expect.poll(async () => (await audioState(page))?.recap, { timeout: 30_000 }).toBe(false);
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
});

test("A7 studio, overlay and compare never ask for audio and get no speech", async ({
  browser,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 56, speed: 20 });
  const asked: string[] = [];
  const pages: Page[] = [];
  const watched: ReturnType<typeof watchSpeech>[] = [];
  for (const path of [
    `/studio/?match=${matchId}`,
    `/overlay/?match=${matchId}&lang=vi`,
    `/compare/?match=${matchId}`,
  ]) {
    const page = await (await browser.newContext()).newPage();
    page.on("request", (r) => {
      const pathname = new URL(r.url()).pathname;
      if (pathname.startsWith("/audio") || pathname.startsWith("/speech")) asked.push(pathname);
    });
    watched.push(watchSpeech(page));
    await page.goto(path);
    pages.push(page);
  }
  await expect
    .poll(() => watched.reduce((sum, w) => sum + w.silent.size, 0), { timeout: 60_000 })
    .toBeGreaterThan(3);
  expect(asked).toEqual([]);
  for (const w of watched) expect(w.lines.size).toBe(0);
  await stopMatch(request, matchId);
  for (const page of pages) await page.context().close();
});

test("A8 the studio restarts from 45': the fan's queue is cleared, then reads the new session", async ({
  browser,
  request,
}) => {
  const matchId = await createMatch(request, { seed: 57, speed: 20 });
  const fan = await (await browser.newContext()).newPage();
  const studio = await (await browser.newContext()).newPage();
  const errors = watchErrors(fan);
  const speech = watchSpeech(fan);
  await listen(fan, matchId);
  await studio.goto(`/studio/?match=${matchId}`);
  await expect
    .poll(async () => (await livePlayed(fan)).length, { timeout: 90_000 })
    .toBeGreaterThanOrEqual(1);
  const before = (await audioState(fan))?.played.length ?? 0;
  studio.once("dialog", (dialog) => void dialog.accept());
  await studio.getByTestId("restart-from").selectOption("45");
  await expect
    .poll(async () => (await socketStats(fan, "fan"))?.reconnects ?? 0, { timeout: 15_000 })
    .toBeGreaterThan(0);
  expect((await audioState(fan))?.on).toBe(true); // a restart keeps the voice on
  await expect
    .poll(async () => (await audioState(fan))?.played.length ?? 0, { timeout: 90_000 })
    .toBeGreaterThan(before);
  const after = (await audioState(fan))?.played.slice(before) ?? [];
  for (const id of after.filter((id) => !id.startsWith("greeting-"))) {
    expect(speech.lines.has(id), id).toBe(true);
  }
  expect((await audioState(fan))?.maxConcurrent).toBe(1);
  expect(errors).toEqual([]);
  await stopMatch(request, matchId);
  for (const page of [fan, studio]) await page.context().close();
});

test("A11 axe on the fan player with the voice on and the recap player open", async ({
  page,
  request,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const matchId = await createMatch(request, { seed: 58, speed: 20, from_minute: 43 });
  await listen(page, matchId);
  await expect(page.getByTestId("voice-status")).toBeVisible();
  expect(await seriousA11y(page)).toEqual([]);
  await page.getByTestId("tab-recap").click();
  await expect(page.getByTestId("recap-listen")).toBeVisible({ timeout: 90_000 });
  await page.getByTestId("recap-listen").click();
  await expect(page.getByTestId("recap-player")).toBeVisible();
  expect(await seriousA11y(page)).toEqual([]);
  await stopMatch(request, matchId);
});

test.describe("speech to text (P2)", () => {
  test.use({ permissions: ["microphone"] });

  test("A10 speak a claim: the box gets the text, nothing is sent; Enter verifies it", async ({
    page,
    request,
  }) => {
    const errors = watchErrors(page);
    const matchId = await createMatch(request, { seed: 60, speed: 20 });
    const verified: string[] = [];
    page.on("request", (r) => {
      if (new URL(r.url()).pathname === "/claims/verify") verified.push(r.url());
    });
    await page.goto(`/studio/?match=${matchId}`);
    const mic = page.getByTestId("mic-button");
    await expect(mic).toBeVisible();
    await mic.click();
    await expect(mic).toHaveAttribute("data-phase", "recording");
    await page.waitForTimeout(1500); // Chromium's fake microphone plays a tone
    const recognized = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/speech/recognize",
    );
    await mic.click();
    const response = await recognized;
    expect(response.status(), await response.text()).toBe(200);
    expect(response.request().headers()["content-type"]).toBe("audio/wav");
    // the studio speaks English by default: the mock's English claim, not sent
    await expect(page.getByTestId("claim-input")).toHaveValue(
      "The home side dominated the second half",
    );
    await expect(page.getByTestId("claim-input")).toBeFocused();
    expect(verified).toEqual([]);
    // the commentator reads it and presses Enter: the Day 4 path
    const verdict = page.waitForResponse((r) => new URL(r.url()).pathname === "/claims/verify");
    await page.getByTestId("claim-input").press("Enter");
    expect((await verdict).ok()).toBeTruthy();
    await expect(page.getByTestId("verdict-card").first()).toBeVisible();
    expect(errors).toEqual([]);
    await stopMatch(request, matchId);
  });
});
