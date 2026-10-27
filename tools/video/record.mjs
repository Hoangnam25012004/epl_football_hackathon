// Records the demo video's scenes on the public Space (docs/video/script.md): Edge (CDP, full
// screen on the 1920x1080 monitor) is driven by Playwright, OBS records the screen and the
// desktop's sound, one file per take -> C:/pundit-video/scenes/<take>.<ext> + <take>.json
// (marks: seconds from the start of the take). Waits a presenter would cut happen off camera.
//   node record.mjs s1 s2 s3 ...     (scenes in order; state shared in state.json)
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { extname } from "node:path";
import { connectObs } from "./obs.mjs";

const require = createRequire("C:/epl football hackathon/frontend/package.json");
const { chromium } = require("@playwright/test");

const BASE = "https://hnam2501-pundit-copilot.hf.space";
const PRIVATE = /\/studio\/\?match=(dominant_loser-42~[a-z0-9]{8})$/;
const VI_CLAIM = "Đội nhà áp đảo hiệp 2";
const EN_CLAIM = "Kestrel took more shots than Harbor";
const STATE = "C:/pundit-video/state.json";
const OUT = "C:/pundit-video/scenes";
mkdirSync(OUT, { recursive: true });

const state = existsSync(STATE) ? JSON.parse(readFileSync(STATE, "utf8")) : {};
const save = () => writeFileSync(STATE, JSON.stringify(state, null, 1));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
const context = browser.contexts()[0];
const page = context.pages()[0] ?? (await context.newPage());
page.setDefaultTimeout(60_000);
const obs = await connectObs();

async function post(path, data) {
  const r = await page.request.post(`${BASE}${path}`, { data, timeout: 60_000 });
  if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`);
  return r.json();
}
async function get(path) {
  const r = await page.request.get(`${BASE}${path}`, { timeout: 60_000 });
  if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`);
  return r.json();
}

async function take(name, body) {
  const marks = [];
  await obs.call("StartRecord");
  const t0 = Date.now();
  const mark = (label, extra = {}) => marks.push({ label, t: (Date.now() - t0) / 1000, ...extra });
  await sleep(600);
  mark("start");
  try {
    await body(mark);
  } finally {
    mark("end");
    const { outputPath } = await obs.call("StopRecord");
    await sleep(2000);
    const dest = `${OUT}/${name}${extname(outputPath)}`;
    renameSync(outputPath, dest);
    writeFileSync(`${OUT}/${name}.json`, JSON.stringify({ file: dest, marks }, null, 1));
    log(`take ${name}: ${marks.at(-1).t.toFixed(1)} s -> ${dest}`);
  }
}

async function typeSlowly(text) {
  const input = page.getByTestId("claim-input");
  await input.click();
  await input.fill("");
  await page.keyboard.type(text, { delay: 45 });
}

async function claimCards() {
  return page.getByTestId("verdict-card").count();
}

const scenes = {
  // 1: the studio of my private demo match, playing at 20x
  async s1() {
    await page.goto(`${BASE}/`);
    await page.getByTestId("start-my-demo").click();
    await page.waitForURL(PRIVATE, { timeout: 90_000 });
    state.a = PRIVATE.exec(page.url())[1];
    save();
    log("match A", state.a);
    const status = page.getByTestId("match-status");
    if ((await status.getAttribute("data-status")) !== "running") await page.getByTestId("play-pause").click();
    await sleep(7000); // a few minutes of the match in the feed
    await take("s1", async () => sleep(9000));
  },

  // 2: two claims, the rules at once; the model's explanation later
  async s2() {
    await page.goto(`${BASE}/studio/?match=${state.a}`);
    await post(`/matches/${state.a}/speed`, { speed: 1000 });
    await page.getByTestId("match-status").and(page.locator("[data-status=finished]")).waitFor({ timeout: 180_000 });
    await sleep(3000);
    await take("s2a", async (mark) => {
      await sleep(800);
      let before = await claimCards();
      await typeSlowly(EN_CLAIM);
      await page.getByTestId("claim-submit").click();
      await page.waitForFunction((n) => document.querySelectorAll("[data-testid=verdict-card]").length > n, before);
      mark("en-verdict");
      await sleep(3500);
      before = await claimCards();
      mark("vi-typing");
      await typeSlowly(VI_CLAIM);
      await page.getByTestId("claim-submit").click();
      await page.waitForFunction((n) => document.querySelectorAll("[data-testid=verdict-card]").length > n, before);
      mark("vi-verdict");
      await sleep(7000);
    });
    const card = page.getByTestId("verdict-card").first();
    await card.and(page.locator("[data-revision='2']")).waitFor({ timeout: 240_000 });
    log("rev.2:", await card.getAttribute("data-model-pass"));
    await sleep(1500);
    await take("s2b", async () => sleep(6500));
  },

  // 2, retake on the finished match: the Vietnamese claim first (its model pass is not queued
  // behind the English one), then the English one; the model's explanation after the cut
  async s2r() {
    await page.goto(`${BASE}/studio/?match=${state.a}`);
    await page.getByTestId("claim-input").waitFor();
    await sleep(2500);
    await take("s2a", async (mark) => {
      await sleep(800);
      let before = await claimCards();
      mark("vi-typing");
      await typeSlowly(VI_CLAIM);
      await page.getByTestId("claim-submit").click();
      await page.waitForFunction((n) => document.querySelectorAll("[data-testid=verdict-card]").length > n, before);
      mark("vi-verdict");
      await sleep(5000);
      before = await claimCards();
      await typeSlowly(EN_CLAIM);
      await page.getByTestId("claim-submit").click();
      await page.waitForFunction((n) => document.querySelectorAll("[data-testid=verdict-card]").length > n, before);
      mark("en-verdict");
      await sleep(4500);
    });
    const vi = page.getByTestId("verdict-card").filter({ hasText: VI_CLAIM }).first();
    await vi.and(page.locator("[data-revision='2']")).waitFor({ timeout: 240_000 });
    const pass = await vi.getAttribute("data-model-pass");
    state.s2pass = pass;
    save();
    log("rev.2 of the Vietnamese claim:", pass, (await vi.getByTestId("claim-source").first().textContent()) ?? "");
    await vi.scrollIntoViewIfNeeded();
    await sleep(1500);
    await take("s2b", async () => sleep(6500));
  },

  // a new private match played to full time (after a redeploy wiped the sessions)
  async prep() {
    const created = await post("/matches", { scenario: "dominant_loser", seed: 42, private: true, speed: 1000, autostart: true });
    state.a = created.match_id;
    save();
    log("match A", state.a);
    await page.goto(`${BASE}/studio/?match=${state.a}`);
    await page.getByTestId("match-status").and(page.locator("[data-status=finished]")).waitFor({ timeout: 180_000 });
    await page.getByTestId("talking-point").first().waitFor({ timeout: 60_000 });
    await sleep(5000);
  },

  // 3: Agent Ops while a claim runs
  async s3() {
    await page.goto(`${BASE}/agents/?match=${state.a}`);
    await page.getByTestId("agent-lanes").waitFor();
    await sleep(2500);
    await take("s3", async (mark) => {
      await sleep(1200);
      mark("claim");
      await post("/claims/verify", { match_id: state.a, text: EN_CLAIM });
      await sleep(9500);
    });
  },

  // 4: Chaos, the model lies; the guardrail blocks it
  async s4() {
    if (!page.url().includes("/agents/")) {
      await page.goto(`${BASE}/agents/?match=${state.a}`);
      await page.getByTestId("agent-lanes").waitFor();
    }
    const lies = page.getByTestId("chaos-lies");
    await take("s4a", async (mark) => {
      await sleep(1000);
      await page.getByTestId("chaos-model_lies").click();
      await page.getByTestId("chaos-state").filter({ hasText: "The model lies" }).waitFor();
      mark("lies-on");
      await sleep(1500);
      post("/claims/verify", { match_id: state.a, text: VI_CLAIM }).catch(() => {});
      mark("claim");
      await sleep(6000);
    });
    // the model's pass (with its lie) takes a while on the free CPU: cut, wait for the block
    const before = (await lies.textContent()) ?? "";
    log("lies before:", before);
    const deadline = Date.now() + 240_000;
    while (Date.now() < deadline) {
      const now = (await lies.textContent().catch(() => "")) ?? "";
      if (now !== before && /reached a screen: 0/.test(now)) break;
      await sleep(2000);
    }
    log("lies after:", await lies.textContent());
    await sleep(1000);
    await take("s4b", async (mark) => {
      await sleep(6000);
      mark("reset");
      await page.getByTestId("chaos-reset").click();
      await page.getByTestId("chaos-state").filter({ hasText: "Nothing is broken." }).waitFor();
      await sleep(2000);
    });
  },

  // 5: a talking point pinned, a lower-third taken on air, the overlay
  async s5() {
    await page.goto(`${BASE}/studio/?match=${state.a}`);
    await page.getByTestId("talking-point").first().waitFor();
    await sleep(2000);
    await take("s5", async (mark) => {
      await sleep(1000);
      await page
        .getByTestId("talking-point")
        .filter({ has: page.getByTestId("tp-pin") })
        .first()
        .getByTestId("tp-pin")
        .click();
      mark("pinned");
      await sleep(1800);
      const takeable = page
        .getByTestId("lower-third-item")
        .filter({ has: page.locator("[data-testid=on-air-take]:not([disabled])") });
      await takeable.first().getByTestId("on-air-take").click();
      mark("taken");
      await sleep(1500);
      await page.goto(`${BASE}/overlay/?match=${state.a}&lang=en`);
      await page.addStyleTag({
        // the page's own rule is `html.overlay-page body {background: transparent !important}`:
        // the same specificity, later in the document, wins (a stand-in for the video behind)
        content:
          "html.overlay-page, html.overlay-page body{background:radial-gradient(ellipse at 50% 35%,#24402c 0%,#14261a 45%,#0b1410 100%) !important}",
      });
      await page.getByTestId("lower-third").waitFor({ timeout: 20_000 });
      mark("overlay");
      await sleep(6000);
    });
    await post(`/matches/${state.a}/on-air`, { action: "clear" }).catch((e) => log("clear:", e.message));
    await post(`/matches/${state.a}/on-air`, { action: "auto" }).catch((e) => log("auto:", e.message));
  },

  // 6: the fan player, Vietnamese with a voice, beginner mode, around the first offside
  async s6() {
    // Kestrel's fan: the first offside is Kestrel's own, so it is read (a rival's offside is a
    // low-relevance line, shown but not read, for a Harbor fan)
    const club = "kestrel_athletic";
    const items = await get(`/matches/${state.a}/commentary?lang=en&mode=casual`);
    const offside = items.find((i) => i.kind === "offside");
    const minute = (offside?.clock_ms ?? 480_000) / 60_000 + ((offside?.period ?? 1) === 2 ? 45 : 0);
    state.offside = offside?.id; // item ids are the same in every session of a scenario and seed
    state.club = club;
    log("offside item", offside?.id, "at minute", minute.toFixed(2));
    // lines before from_minute are caught up without a card: start earlier and play the minutes
    // before the offside live (off camera), so the feed is not empty under the voice-over
    const created = await post("/matches", {
      scenario: "dominant_loser", seed: 42, private: true, speed: 20,
      from_minute: Math.max(0, minute - 4), autostart: false,
    });
    state.b = created.match_id;
    save();
    log("match B", state.b);
    await page.goto(`${BASE}/fan/?match=${state.b}`);
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); }); // the profile setup again
    await page.reload();
    await page.getByTestId("mode-casual").waitFor();
    // off camera: the profile, then the voice; turning it on reads the AI-voice greeting first
    // (a line waiting behind it for over 6 s is dropped as stale): let it finish
    await page.getByTestId("mode-casual").click();
    await page.getByRole("radio", { name: "Tiếng Việt" }).click();
    await page.getByTestId(`club-${club}`).click();
    await page.getByTestId("beginner-setup").check();
    await page.getByTestId("profile-start").click();
    await page.getByTestId("voice-toggle").waitFor();
    await sleep(1500);
    await page.getByTestId("voice-toggle").click();
    await page.waitForFunction(() => window.punditAudio?.played?.includes("greeting-1") && !window.punditAudio?.speakingId, null, { timeout: 30_000 });
    await post(`/matches/${state.b}/start`, {});
    const stopAt = minute - 0.7;
    for (let i = 0; i < 200; i++) {
      const last = (await get(`/matches/${state.b}/events?last=1`)).events?.[0];
      const now = last ? last.clock_ms / 60_000 + (last.period === 2 ? 45 : 0) : 0;
      if (now >= stopAt) break;
      await sleep(250);
    }
    await post(`/matches/${state.b}/pause`, {});
    await post(`/matches/${state.b}/speed`, { speed: 8 });
    await page.waitForFunction(() => !window.punditAudio?.speakingId, null, { timeout: 30_000 });
    await sleep(2500);
    const spoken = [];
    await take("s6", async (mark) => {
      await sleep(7600); // the voice-over of the scene first
      mark("match-start");
      await post(`/matches/${state.b}/resume`, {});
      let current = null;
      const t0 = Date.now();
      while (Date.now() - t0 < 60_000) {
        const id = await page
          .locator("[data-testid=commentary-card][data-speaking=true]")
          .first()
          .getAttribute("data-id", { timeout: 300 })
          .catch(() => null);
        if (id !== current) {
          if (current) mark("spoken-end", { id: current });
          if (id) {
            mark("spoken-start", { id });
            spoken.push(id);
          }
          current = id;
        }
        const target = state.offside;
        const done = target ? spoken.includes(target) && id !== target : spoken.length >= 3 && !id;
        if (done) break;
        await sleep(200);
      }
      await sleep(1500);
    });
    state.spoken = spoken;
    save();
    await post(`/matches/${state.b}/pause`, {}).catch(() => {});
  },

  // 7: the architecture and a live MCP answer from the public Space
  async s7() {
    const rpc = (id, method, params) => ({ jsonrpc: "2.0", id, method, params });
    const headers = { Accept: "application/json, text/event-stream", "Content-Type": "application/json" };
    const r = await page.request.post(`${BASE}/mcp`, {
      headers,
      data: rpc(1, "tools/call", { name: "verify_claim", arguments: { match_id: "dominant_loser-42", text: VI_CLAIM } }),
      timeout: 60_000,
    });
    const answer = (await r.json()).result?.structuredContent ?? {};
    writeFileSync("C:/pundit-video/mcp-answer.json", JSON.stringify(answer, null, 1));
    const html = readFileSync("C:/pundit-video/tools/arch.html", "utf8").replace(
      "__MCP_ANSWER__",
      JSON.stringify(answer).replace(/</g, "\\u003c"),
    );
    await page.goto("about:blank");
    await page.setContent(html, { waitUntil: "networkidle" });
    await page.waitForFunction(() => document.querySelector("#diagram svg"), null, { timeout: 30_000 });
    await sleep(1200);
    await take("s7", async () => sleep(13500));
  },

  // 8: the landing page
  async s8() {
    await page.goto(`${BASE}/`);
    await page.getByTestId("start-my-demo").waitFor();
    await sleep(1500);
    await take("s8", async () => sleep(4000));
  },
};

for (const name of process.argv.slice(2)) {
  log("scene", name);
  await scenes[name]();
}
obs.close();
await browser.close().catch(() => {});
