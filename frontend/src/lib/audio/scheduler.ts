// The fan player's reading queue (docs/day5-design.md section 10.3): a pure step function, so
// every rule is tested without audio. One voice at a time; at most three lines waiting; a goal
// cuts in front of a quiet line; a line too old to be news is dropped; the server's clip first,
// the browser's voice when there is none (or after three failures in a row, for a minute).
import type { SpeechCue } from "@/lib/contracts";

export type Source = "server" | "browser";

export type AudioItem = {
  id: string; // the commentary item
  n: number; // message number: arrival order
  excitement: number; // 0-3, from the server
  clockMs: number;
  cue: SpeechCue;
  arrivedAt: number; // client clock (ms)
  greeting?: boolean; // the voice's greeting: no connection's line, a new hello keeps it
};

export type Playing = { item: AudioItem; source: Source; startedAt: number };

export type Counts = {
  played: number;
  dropped: number;
  preempted: number;
  failed: number;
  fallbacks: number;
  skipped: number;
};

export type QueueState = {
  muted: boolean;
  paused: boolean; // the match is paused: finish the line, start no other
  recap: boolean; // a recap is playing: the live queue waits
  browserAvailable: boolean; // the browser has a voice for the language
  current: Playing | null;
  waiting: AudioItem[];
  recent: string[]; // ids already taken (a reconnection never reads a line twice)
  serverFailures: number;
  browserUntil: number; // the client's breaker: browser only until then
  counts: Counts;
};

export type Input =
  | { type: "enqueue"; item: AudioItem }
  | { type: "ended"; id: string }
  | { type: "failed"; id: string; source: Source }
  | { type: "tick" }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "mute" }
  | { type: "unmute" }
  | { type: "recapStart" }
  | { type: "recapEnd" }
  | { type: "reset" } // a new hello / resync: what waited is stale
  | { type: "clear" } // a restart or another match: stop and forget
  | { type: "skip" }
  | { type: "browser"; available: boolean };

export type Effect =
  { type: "play"; item: AudioItem; source: Source } | { type: "stop"; id: string };

export const MAX_WAITING = 3;
export const STALE_MS = [6_000, 8_000, 12_000, 20_000] as const; // by excitement 0-3
export const PREEMPT_AFTER_MS = 1_000;
export const SERVER_FAILURES_TO_SWITCH = 3;
export const BROWSER_ONLY_MS = 60_000;
export const RECENT_LIMIT = 100;
const NEVER_READ = new Set(["guard", "speed"]);

export function initialQueue(browserAvailable = false): QueueState {
  return {
    muted: false,
    paused: false,
    recap: false,
    browserAvailable,
    current: null,
    waiting: [],
    recent: [],
    serverFailures: 0,
    browserUntil: 0,
    counts: { played: 0, dropped: 0, preempted: 0, failed: 0, fallbacks: 0, skipped: 0 },
  };
}

const staleAfter = (item: AudioItem): number =>
  STALE_MS[Math.max(0, Math.min(3, item.excitement))] ?? STALE_MS[0];

/** Which waiting lines to keep when there are too many: the most exciting, then the newest;
 * kept in arrival order. */
export function keepItems(waiting: readonly AudioItem[]): AudioItem[] {
  const kept = [...waiting]
    .sort((a, b) => b.excitement - a.excitement || b.n - a.n)
    .slice(0, MAX_WAITING);
  return kept.sort((a, b) => a.n - b.n);
}

type Draft = { state: QueueState; effects: Effect[] };

function count(draft: Draft, key: keyof Counts, by = 1): void {
  draft.state.counts = { ...draft.state.counts, [key]: draft.state.counts[key] + by };
}

function needsBrowser(item: AudioItem): boolean {
  return !item.cue.audio;
}

function sourceFor(state: QueueState, item: AudioItem, now: number): Source | null {
  if (needsBrowser(item) || now < state.browserUntil) {
    if (state.browserAvailable) return "browser";
    return needsBrowser(item) ? null : "server";
  }
  return "server";
}

function prune(draft: Draft, now: number): void {
  const fresh = draft.state.waiting.filter((item) => now - item.arrivedAt <= staleAfter(item));
  count(draft, "dropped", draft.state.waiting.length - fresh.length);
  draft.state.waiting = fresh;
}

function start(draft: Draft, item: AudioItem, source: Source, now: number): void {
  draft.state.current = { item, source, startedAt: now };
  draft.effects.push({ type: "play", item, source });
}

/** Start the next line when nothing is read and nothing holds the queue. */
function advance(draft: Draft, now: number): void {
  const s = draft.state;
  if (s.current || s.muted || s.paused || s.recap) return;
  prune(draft, now);
  while (draft.state.waiting.length) {
    const waiting = draft.state.waiting;
    const goal = waiting.find((item) => item.excitement >= 3);
    const next = goal ?? waiting[0];
    if (!next) return;
    draft.state.waiting = waiting.filter((item) => item !== next);
    const source = sourceFor(draft.state, next, now);
    if (source === null) {
      count(draft, "dropped");
      continue;
    }
    start(draft, next, source, now);
    return;
  }
}

function stopCurrent(draft: Draft): void {
  if (!draft.state.current) return;
  draft.effects.push({ type: "stop", id: draft.state.current.item.id });
  draft.state.current = null;
}

function enqueue(draft: Draft, item: AudioItem, now: number): void {
  const s = draft.state;
  const known =
    s.recent.includes(item.id) ||
    s.current?.item.id === item.id ||
    s.waiting.some((waiting) => waiting.id === item.id);
  if (s.muted || known) return;
  if (item.cue.note && NEVER_READ.has(item.cue.note)) return;
  s.recent = [...s.recent, item.id].slice(-RECENT_LIMIT);
  if (needsBrowser(item) && !s.browserAvailable) {
    count(draft, "dropped"); // no clip and no voice in this browser: the text is enough
    return;
  }
  const current = s.current;
  if (
    current &&
    item.excitement >= 3 &&
    current.item.excitement <= 1 &&
    now - current.startedAt >= PREEMPT_AFTER_MS &&
    !s.paused &&
    !s.recap
  ) {
    const source = sourceFor(s, item, now);
    if (source !== null) {
      stopCurrent(draft);
      count(draft, "preempted");
      start(draft, item, source, now);
      return;
    }
  }
  const waiting = [...s.waiting, item];
  const kept = waiting.length > MAX_WAITING ? keepItems(waiting) : waiting;
  count(draft, "dropped", waiting.length - kept.length);
  draft.state.waiting = kept;
  advance(draft, now);
}

function failed(draft: Draft, id: string, source: Source, now: number): void {
  const current = draft.state.current;
  if (!current || current.item.id !== id) return;
  count(draft, "failed");
  draft.state.current = null;
  if (source === "server") {
    const failures = draft.state.serverFailures + 1;
    draft.state.serverFailures = failures;
    if (failures >= SERVER_FAILURES_TO_SWITCH) {
      draft.state.browserUntil = now + BROWSER_ONLY_MS;
      draft.state.serverFailures = 0;
    }
    const fresh = now - current.item.arrivedAt <= staleAfter(current.item);
    if (draft.state.browserAvailable && fresh && !draft.state.muted) {
      count(draft, "fallbacks"); // the same words, read by the browser right away
      start(draft, current.item, "browser", now);
      return;
    }
  }
  advance(draft, now);
}

export function step(state: QueueState, input: Input, now: number): Draft {
  const draft: Draft = { state: { ...state }, effects: [] };
  const s = draft.state;
  switch (input.type) {
    case "enqueue":
      enqueue(draft, input.item, now);
      break;
    case "ended":
      if (s.current?.item.id === input.id) {
        if (s.current.source === "server") s.serverFailures = 0;
        s.current = null;
        count(draft, "played");
        advance(draft, now);
      }
      break;
    case "failed":
      failed(draft, input.id, input.source, now);
      break;
    case "tick":
      if (!s.current) advance(draft, now);
      else prune(draft, now);
      break;
    case "pause":
      s.paused = true;
      break;
    case "resume":
      s.paused = false;
      advance(draft, now);
      break;
    case "mute":
      s.muted = true;
      stopCurrent(draft);
      count(draft, "dropped", s.waiting.length);
      s.waiting = [];
      break;
    case "unmute":
      s.muted = false;
      break;
    case "recapStart":
      s.recap = true;
      stopCurrent(draft);
      break;
    case "recapEnd":
      s.recap = false;
      advance(draft, now);
      break;
    case "reset": {
      const kept = s.waiting.filter((item) => item.greeting);
      count(draft, "dropped", s.waiting.length - kept.length);
      s.waiting = kept;
      break;
    }
    case "clear":
      stopCurrent(draft);
      count(draft, "dropped", s.waiting.length);
      s.waiting = [];
      break;
    case "skip":
      if (s.current) {
        stopCurrent(draft);
        count(draft, "skipped");
        advance(draft, now);
      }
      break;
    case "browser":
      s.browserAvailable = input.available;
      break;
  }
  return draft;
}
