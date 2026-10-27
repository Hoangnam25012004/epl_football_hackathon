// The English voice-over: Edge reads each line of docs/video/voiceover.txt with a natural voice,
// OBS records the desktop's sound, one recording per line -> C:/pundit-video/narration/NN.wav.
// node narrate.mjs [first] [last]   (1-based lines; default all)
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { connectObs } from "./obs.mjs";

const require = createRequire("C:/epl football hackathon/frontend/package.json");
const { chromium } = require("@playwright/test");
const VOICE = /Andrew Multilingual Online \(Natural\) - English \(United States\)/;
const lines = readFileSync("C:/epl football hackathon/docs/video/voiceover.txt", "utf8")
  .split(/\r?\n/)
  .filter((l) => l.trim());
const first = Number(process.argv[2] ?? 1);
const last = Number(process.argv[3] ?? lines.length);
mkdirSync("C:/pundit-video/narration", { recursive: true });

const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
const page = browser.contexts()[0].pages()[0];
await page.goto("about:blank");
await page.evaluate(() => document.body.style.background = "#0b1220");
for (let i = 0; i < 20; i++) {
  const ok = await page.evaluate((src) => speechSynthesis.getVoices().some((v) => new RegExp(src).test(v.name)), VOICE.source);
  if (ok) break;
  await page.waitForTimeout(1000);
}
const obs = await connectObs();
for (let n = first; n <= last; n++) {
  const text = lines[n - 1];
  await obs.call("StartRecord");
  await page.waitForTimeout(1200);
  const spoken = await page.evaluate(
    ({ text, src }) =>
      new Promise((resolve) => {
        const u = new SpeechSynthesisUtterance(text);
        u.voice = speechSynthesis.getVoices().find((v) => new RegExp(src).test(v.name)) ?? null;
        u.lang = "en-US";
        u.rate = 1.0;
        const began = performance.now();
        u.onend = () => resolve({ ok: true, ms: Math.round(performance.now() - began), voice: u.voice?.name });
        u.onerror = (e) => resolve({ ok: false, error: e.error });
        speechSynthesis.speak(u);
      }),
    { text, src: VOICE.source },
  );
  await page.waitForTimeout(800);
  const { outputPath } = await obs.call("StopRecord");
  await page.waitForTimeout(1500); // the file is closed
  const wav = `C:/pundit-video/narration/${String(n).padStart(2, "0")}.wav`;
  // trim the silence before and after the line (the voice is the only sound)
  execFileSync("ffmpeg", [
    "-y", "-loglevel", "error", "-i", outputPath, "-vn", "-ac", "2", "-ar", "48000",
    "-af", "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse",
    wav,
  ]);
  rmSync(outputPath);
  const seconds = execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", wav]).toString().trim();
  console.log(`line ${n}: ${spoken.ok ? "spoken" : "FAILED " + spoken.error} in ${spoken.ms} ms; wav ${seconds} s; ${spoken.voice ?? ""}`);
}
obs.close();
await browser.close().catch(() => {});
