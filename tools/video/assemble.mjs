// Cuts the takes, lays the voice-over, burns English subtitles for every Vietnamese part, adds the
// closing card -> C:/pundit-video/pundit-copilot-demo.mp4 (+ .srt for YouTube).
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const DIR = "C:/pundit-video";
const SEG = `${DIR}/segments`;
mkdirSync(SEG, { recursive: true });
const BASE = "https://hnam2501-pundit-copilot.hf.space";
const state = JSON.parse(readFileSync(`${DIR}/state.json`, "utf8"));
const FONT = "C\\:/Windows/Fonts/segoeui.ttf";
const FONT_BOLD = "C\\:/Windows/Fonts/segoeuib.ttf";

const run = (args) => execFileSync("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", ...args], { stdio: "inherit" });
const duration = (file) =>
  Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file]).toString());
const marks = (take) => JSON.parse(readFileSync(`${DIR}/scenes/${take}.json`, "utf8")).marks;
const at = (take, label, n = 0) => marks(take).filter((m) => m.label === label)[n]?.t;
const narration = (n) => `${DIR}/narration/${String(n).padStart(2, "0")}.wav`;

const subtitles = []; // { start, end, text } in seconds of the final video: burned in
const captions = []; // the English voice-over too, for the YouTube caption file
let clock = 0;
const voiceOver = readFileSync("C:/epl football hackathon/docs/video/voiceover.txt", "utf8").split(/\r?\n/).filter((l) => l.trim());
function caption(n, start) {
  const text = voiceOver[n - 1];
  const length = duration(narration(n));
  const pieces = chunks(text, 84);
  const total = pieces.reduce((s, p) => s + p.length, 0);
  let t = start;
  for (const p of pieces) {
    const d = (length * p.length) / total;
    captions.push({ start: t, end: t + d - 0.05, text: p });
    t += d;
  }
}

/** One segment: pieces of takes [take, from, to], the narration n delayed by `delay`, the takes'
 * own sound at `own` (0 = muted), subtitles relative to the segment. */
function segment(name, pieces, { n, delay = 0.25, own = 0, subs = [] }) {
  const inputs = [];
  const filters = [];
  pieces.forEach(([take, from, to], i) => {
    inputs.push("-ss", from.toFixed(3), "-to", to.toFixed(3), "-i", `${DIR}/scenes/${take}.mp4`);
    filters.push(
      `[${i}:v]fps=30,scale=1920:1080,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v${i}]`,
      `[${i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,volume=${own}[a${i}]`,
    );
  });
  const k = pieces.length;
  filters.push(`${pieces.map((_, i) => `[v${i}][a${i}]`).join("")}concat=n=${k}:v=1:a=1[v][own]`);
  if (n) {
    inputs.push("-i", narration(n));
    filters.push(`[${k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay=${Math.round(delay * 1000)}:all=1[nar]`);
    filters.push(`[own][nar]amix=inputs=2:duration=first:normalize=0[a]`);
  } else {
    filters.push(`[own]anull[a]`);
  }
  const out = `${SEG}/${name}.mp4`;
  run([...inputs, "-filter_complex", filters.join(";"), "-map", "[v]", "-map", "[a]",
       "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k", out]);
  const length = duration(out);
  const narr = n ? duration(narration(n)) : 0;
  if (narr + delay > length + 0.05) console.warn(`! ${name}: narration ${narr.toFixed(1)} s + ${delay} > ${length.toFixed(1)} s`);
  for (const s of subs) subtitles.push({ start: clock + s.start, end: clock + Math.min(s.end, length), text: s.text });
  if (n) caption(n, clock + delay);
  console.log(`${name}: ${length.toFixed(2)} s (at ${clock.toFixed(2)})`);
  clock += length;
  return out;
}

function card(name, seconds, n, delay) {
  const out = `${SEG}/${name}.mp4`;
  const text = (t, size, y, font = FONT, color = "white") =>
    `drawtext=fontfile='${font}':text='${t}':fontsize=${size}:fontcolor=${color}:x=(w-text_w)/2:y=${y}`;
  const vf = [
    text("Pundit Copilot", 96, 330, FONT_BOLD),
    text("Never wrong on air.", 54, 470),
    text("Live demo\\: hnam2501-pundit-copilot.hf.space", 34, 610, FONT, "0xB8C4DA"),
    text("Microsoft Agent Framework · Microsoft Foundry Local · GitHub Actions · MCP", 30, 670, FONT, "0x93A1BD"),
    text("SYNTHETIC DATA ONLY · Not affiliated with any league or club", 30, 860, FONT_BOLD, "0xF5B84A"),
  ].join(",");
  run(["-f", "lavfi", "-i", `color=c=0x0b1220:s=1920x1080:r=30:d=${seconds}`, "-i", narration(n),
       "-filter_complex", `[0:v]${vf},format=yuv420p[v];[1:a]aresample=48000,aformat=channel_layouts=stereo,adelay=${Math.round(delay * 1000)}:all=1,apad[a]`,
       "-map", "[v]", "-map", "[a]", "-t", String(seconds), "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "192k", out]);
  caption(n, clock + delay);
  console.log(`${name}: ${seconds} s (at ${clock.toFixed(2)})`);
  clock += seconds;
  return out;
}

// English for what the fan heard: the same items, rendered for an English casual beginner
async function englishOf(ids) {
  const r = await fetch(`${BASE}/matches/${state.b}/commentary?lang=en&mode=casual&beginner=1&club=${state.club ?? "harbor_lions"}`);
  const views = r.ok ? await r.json() : [];
  const byId = new Map(views.map((v) => [v.id, v]));
  return ids.map((id) => {
    const v = byId.get(id);
    if (!v) return null;
    const parts = [v.framing, v.text].filter(Boolean).join(" ");
    const rule = v.explanation ? [v.explanation.title, v.explanation.text].filter(Boolean).join(": ") : "";
    return { id, line: parts, rule };
  });
}

// a text in subtitle-sized pieces: sentences (not after an initial such as "M."), long ones cut
// at a comma near the middle
function chunks(text, max = 92) {
  const sentences = text.split(/(?<=[^A-Z][.!?])\s+(?=[A-Z“"])/).map((s) => s.trim()).filter(Boolean);
  const out = [];
  for (const sentence of sentences) {
    let rest = sentence;
    while (rest.length > max) {
      const commas = [...rest.matchAll(/, /g)].map((m) => m.index).filter((i) => i > 25 && rest.length - i > 25);
      const middle = rest.length / 2;
      const cut = commas.sort((a, b) => Math.abs(a - middle) - Math.abs(b - middle))[0];
      if (cut === undefined) break;
      out.push(rest.slice(0, cut + 1));
      rest = rest.slice(cut + 2);
    }
    out.push(rest);
  }
  return out;
}

// pieces of a text over [start, end + 1 s], by length, at least 1.2 s each (the subtitles may
// stay a second after the voice stops; the segment's end clips them)
function spread(text, start, end) {
  const pieces = chunks(text);
  const span = end - start + 1.0;
  const total = pieces.reduce((n, p) => n + p.length, 0);
  let durations = pieces.map((p) => (span * p.length) / total);
  const short = durations.filter((d) => d < 1.2).length;
  if (short && short < pieces.length) {
    const fixed = 1.2 * short;
    const rest = durations.filter((d) => d >= 1.2).reduce((a, b) => a + b, 0);
    durations = durations.map((d) => (d < 1.2 ? 1.2 : (d * (span - fixed)) / rest));
  }
  let t = start;
  return pieces.map((p, i) => {
    const sub = { start: t, end: t + durations[i] - 0.05, text: p };
    t += durations[i];
    return sub;
  });
}

const parts = [];
// 1 · the studio at 20x
const s1 = at("s1", "start");
parts.push(segment("01", [["s1", s1, s1 + 7.2]], { n: 1, delay: 0.3 }));

// 2 · claims: rules now; the model's explanation (cut while it ran)
{
  const a0 = at("s2a", "start");
  const a1 = at("s2a", "end");
  const viTyping = at("s2a", "vi-typing") - a0;
  const b0 = at("s2b", "start");
  parts.push(segment("02", [["s2a", a0, a1], ["s2b", b0, b0 + 5.5]], {
    n: 2, delay: 0.4,
    subs: [{ start: viTyping, end: viTyping + 6.5, text: "“Đội nhà áp đảo hiệp 2” = “The home side dominated the second half”" }],
  }));
}

// 3 · Agent Ops
{
  const t0 = at("s3", "start");
  parts.push(segment("03", [["s3", t0, Math.min(at("s3", "end"), t0 + 10.2)]], { n: 3, delay: 0.3 }));
}

// 4 · Chaos: the model lies, the guardrail blocks it
{
  const a0 = at("s4a", "start");
  const b0 = at("s4b", "start");
  parts.push(segment("04", [["s4a", a0, Math.min(at("s4a", "end"), a0 + 6.2)], ["s4b", b0, Math.min(at("s4b", "end"), b0 + 7.8)]], { n: 4, delay: 0.3 }));
}

// 5 · talking point, lower-third on air
{
  const t0 = at("s5", "start");
  parts.push(segment("05", [["s5", t0, Math.min(at("s5", "end"), t0 + 11.5)]], { n: 5, delay: 0.3 }));
}

// 6 · the fan player, Vietnamese with a voice (its own sound at full volume)
{
  const t0 = at("s6", "start");
  const end = at("s6", "end");
  const spokenStarts = marks("s6").filter((m) => m.label === "spoken-start");
  const spokenEnds = marks("s6").filter((m) => m.label === "spoken-end");
  const english = await englishOf(spokenStarts.map((m) => m.id));
  const subs = [{ start: 0.3, end: 7.6, text: "Fan view: casual · Vietnamese · a Kestrel fan · beginner mode" }];
  spokenStarts.forEach((m, i) => {
    const en = english[i];
    if (!en) return;
    const stop = (spokenEnds.find((e) => e.id === m.id)?.t ?? end) - t0;
    const begin = m.t - t0;
    if (!en.rule) {
      subs.push(...spread(en.line, begin, stop));
      return;
    }
    // the line itself is short to read; the rule's explanation takes the rest of the time
    const lineEnd = begin + Math.min(2.2, (stop - begin) / 3);
    subs.push({ start: begin, end: lineEnd - 0.05, text: en.line });
    subs.push(...spread(en.rule, lineEnd, stop));
  });
  parts.push(segment("06", [["s6", t0, end]], { n: 6, delay: 0.3, own: 1, subs }));
}

// 7 · architecture + a live MCP answer
{
  const t0 = at("s7", "start");
  parts.push(segment("07", [["s7", t0, Math.min(at("s7", "end"), t0 + 13.2)]], { n: 7, delay: 0.3 }));
}

// 8 · landing page, then the closing card with the last line
{
  const t0 = at("s8", "start");
  parts.push(segment("08a", [["s8", t0, t0 + 2.2]], { n: null }));
}
parts.push(card("08b", 5.0, 8, 0.3));

const list = `${SEG}/list.txt`;
writeFileSync(list, parts.map((p) => `file '${p}'`).join("\n"));
const joined = `${SEG}/joined.mp4`;
run(["-f", "concat", "-safe", "0", "-i", list, "-c", "copy", joined]);

const srtTime = (s) => {
  const ms = Math.round(s * 1000);
  const h = String(Math.floor(ms / 3_600_000)).padStart(2, "0");
  const m = String(Math.floor((ms % 3_600_000) / 60_000)).padStart(2, "0");
  const sec = String(Math.floor((ms % 60_000) / 1000)).padStart(2, "0");
  return `${h}:${m}:${sec},${String(ms % 1000).padStart(3, "0")}`;
};
const toSrt = (list) =>
  list.map((s, i) => `${i + 1}\n${srtTime(s.start)} --> ${srtTime(s.end)}\n${s.text}\n`).join("\n");
writeFileSync(`${DIR}/pundit-copilot-demo.srt`, toSrt(subtitles), "utf8");
// YouTube: the voice-over and the translations of what is said in Vietnamese
const all = [...captions, ...subtitles].sort((a, b) => a.start - b.start);
writeFileSync(`${DIR}/pundit-copilot-demo.en.srt`, toSrt(all), "utf8");
const style = "FontName=Segoe UI,FontSize=17,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Outline=6,Shadow=0,MarginV=36";
run(["-i", joined, "-vf", `subtitles='${DIR.replace(":", "\\:")}/pundit-copilot-demo.srt':force_style='${style}'`,
     "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-c:a", "copy", `${DIR}/pundit-copilot-demo.mp4`]);
console.log(`total ${duration(`${DIR}/pundit-copilot-demo.mp4`).toFixed(2)} s, ${subtitles.length} subtitles`);
