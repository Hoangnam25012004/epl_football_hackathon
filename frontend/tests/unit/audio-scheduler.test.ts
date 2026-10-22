// The fan player's reading queue (docs/day5-design.md section 10.3): every rule R1-R12 on an
// example, then the invariants I1-I8 on random input sequences (fast-check).
import fc from "fast-check";
import { describe, expect, it } from "vitest";
import type { SpeechCue } from "@/lib/contracts";
import {
  BROWSER_ONLY_MS,
  MAX_WAITING,
  PREEMPT_AFTER_MS,
  RECENT_LIMIT,
  STALE_MS,
  initialQueue,
  keepItems,
  step,
  type AudioItem,
  type Effect,
  type Input,
  type QueueState,
} from "@/lib/audio/scheduler";

function cue(withAudio = true, note: SpeechCue["note"] = null): SpeechCue {
  return {
    lang: "vi-VN",
    voice: "vi-VN-HoaiMyNeural",
    gender: "female",
    preset: "calm",
    segments: [{ text: "Phút hai mươi bảy", preset: "calm", rate: 1, pitch: 1, volume: 1 }],
    audio: withAudio
      ? {
          clip_id: "0".repeat(32),
          url: `/audio/${"0".repeat(32)}.wav`,
          media_type: "audio/wav",
          duration_ms: 2000,
          exact: true,
        }
      : null,
    note: withAudio ? null : (note ?? "server_off"),
  };
}

function item(n: number, excitement = 0, at = 0, withAudio = true): AudioItem {
  return { id: `c${n}`, n, excitement, clockMs: n * 1000, cue: cue(withAudio), arrivedAt: at };
}

/** A queue that is on (unmuted), with or without a browser voice. */
function on(browser = true): QueueState {
  return initialQueue(browser);
}

/** Apply inputs one after the other at the given times; collect the effects. */
function run(
  state: QueueState,
  inputs: [Input, number][],
): { state: QueueState; effects: Effect[] } {
  const effects: Effect[] = [];
  let current = state;
  for (const [input, now] of inputs) {
    const result = step(current, input, now);
    current = result.state;
    effects.push(...result.effects);
  }
  return { state: current, effects };
}

const plays = (effects: Effect[]) =>
  effects.flatMap((e) => (e.type === "play" ? [`${e.item.id}:${e.source}`] : []));

describe("scheduler rules", () => {
  it("R1: takes a line once, never while muted, never a guard or speed note", () => {
    const first = run(on(), [
      [{ type: "enqueue", item: item(1) }, 0],
      [{ type: "ended", id: "c1" }, 100],
      [{ type: "enqueue", item: item(1) }, 200], // the same line again (a resend)
    ]);
    expect(plays(first.effects)).toEqual(["c1:server"]);

    const muted = run(on(), [
      [{ type: "mute" }, 0],
      [{ type: "enqueue", item: item(2) }, 1],
    ]);
    expect(muted.effects).toEqual([]);
    expect(muted.state.recent).toEqual([]); // not even remembered: it was never taken

    for (const note of ["guard", "speed"] as const) {
      const skipped = run(on(), [
        [{ type: "enqueue", item: { ...item(3), cue: { ...cue(false, note), segments: [] } } }, 0],
      ]);
      expect(skipped.effects).toEqual([]);
    }
  });

  it("R1: remembers the last 100 ids only", () => {
    let state = on();
    for (let n = 1; n <= RECENT_LIMIT + 5; n += 1) {
      state = step(state, { type: "enqueue", item: item(n) }, n).state;
      state = step(state, { type: "ended", id: `c${n}` }, n).state;
    }
    expect(state.recent).toHaveLength(RECENT_LIMIT);
    expect(state.recent[0]).toBe("c6");
  });

  it("R2: a goal cuts in front of a quiet line read for a second or more", () => {
    const early = run(on(), [
      [{ type: "enqueue", item: item(1, 1, 0) }, 0],
      [{ type: "enqueue", item: item(2, 3, PREEMPT_AFTER_MS - 1) }, PREEMPT_AFTER_MS - 1],
    ]);
    expect(early.effects.map((e) => e.type)).toEqual(["play"]); // too early: the goal waits
    expect(early.state.waiting.map((i) => i.id)).toEqual(["c2"]);

    const cut = run(on(), [
      [{ type: "enqueue", item: item(1, 1, 0) }, 0],
      [{ type: "enqueue", item: item(2, 3, PREEMPT_AFTER_MS) }, PREEMPT_AFTER_MS],
    ]);
    expect(cut.effects).toEqual([
      expect.objectContaining({ type: "play" }),
      { type: "stop", id: "c1" },
      expect.objectContaining({ type: "play", source: "server" }),
    ]);
    expect(cut.state.current?.item.id).toBe("c2");
    expect(cut.state.counts.preempted).toBe(1);

    const lively = run(on(), [
      [{ type: "enqueue", item: item(1, 2, 0) }, 0],
      [{ type: "enqueue", item: item(2, 3, 5000) }, 5000],
    ]);
    expect(lively.state.current?.item.id).toBe("c1"); // an exciting line is not cut
  });

  it("R3: at most three waiting; the most exciting, then the newest, kept in arrival order", () => {
    const waiting = [item(1, 0), item(2, 3), item(3, 1), item(4, 0), item(5, 2)];
    expect(keepItems(waiting).map((i) => i.id)).toEqual(["c2", "c3", "c5"]);
    const full = run(on(), [
      [{ type: "enqueue", item: item(1, 2, 0) }, 0], // playing
      [{ type: "enqueue", item: item(2, 0, 10) }, 10],
      [{ type: "enqueue", item: item(3, 0, 20) }, 20],
      [{ type: "enqueue", item: item(4, 0, 30) }, 30],
      [{ type: "enqueue", item: item(5, 1, 40) }, 40],
    ]);
    expect(full.state.waiting.map((i) => i.id)).toEqual(["c3", "c4", "c5"]);
    expect(full.state.counts.dropped).toBe(1);
    expect(full.state.waiting.length).toBeLessThanOrEqual(MAX_WAITING);
  });

  it("R4: a line waiting too long is dropped (6, 8, 12, 20 s by excitement)", () => {
    for (const excitement of [0, 1, 2]) {
      const limit = STALE_MS[excitement] ?? 0;
      const fresh = run(on(), [
        [{ type: "enqueue", item: item(1, 2, 0) }, 0],
        [{ type: "enqueue", item: item(2, excitement, 0) }, 0],
        [{ type: "ended", id: "c1" }, limit],
      ]);
      expect(fresh.state.current?.item.id).toBe("c2");
      const stale = run(on(), [
        [{ type: "enqueue", item: item(1, 2, 0) }, 0],
        [{ type: "enqueue", item: item(2, excitement, 0) }, 0],
        [{ type: "ended", id: "c1" }, limit + 1],
      ]);
      expect(stale.state.current).toBeNull();
      expect(stale.state.counts.dropped).toBe(1);
    }
    const ticked = run(on(), [
      [{ type: "enqueue", item: item(1, 2, 0) }, 0],
      [{ type: "enqueue", item: item(2, 0, 0) }, 0],
      [{ type: "tick" }, (STALE_MS[0] ?? 0) + 1],
    ]);
    expect(ticked.state.waiting).toEqual([]); // pruned while the first line still plays
  });

  it("R5: a goal first, otherwise the earliest", () => {
    const result = run(on(), [
      [{ type: "enqueue", item: item(1, 2, 0) }, 0],
      [{ type: "enqueue", item: item(2, 0, 10) }, 10],
      [{ type: "enqueue", item: item(3, 3, 20) }, 20], // c1 is lively: no cut, the goal waits
      [{ type: "enqueue", item: item(4, 1, 30) }, 30],
      [{ type: "ended", id: "c1" }, 100],
      [{ type: "ended", id: "c3" }, 200],
      [{ type: "ended", id: "c2" }, 300],
    ]);
    expect(plays(result.effects)).toEqual(["c1:server", "c3:server", "c2:server", "c4:server"]);
  });

  it("R6: no clip of the server means the browser's voice", () => {
    const result = run(on(), [[{ type: "enqueue", item: item(1, 0, 0, false) }, 0]]);
    expect(plays(result.effects)).toEqual(["c1:browser"]);
  });

  it("R7: when a line ends, the next one starts", () => {
    const result = run(on(), [
      [{ type: "enqueue", item: item(1) }, 0],
      [{ type: "enqueue", item: item(2) }, 1],
      [{ type: "ended", id: "c1" }, 2],
    ]);
    expect(plays(result.effects)).toEqual(["c1:server", "c2:server"]);
    expect(result.state.counts.played).toBe(1);
    expect(run(result.state, [[{ type: "ended", id: "nope" }, 3]]).effects).toEqual([]);
  });

  it("R8: a server failure is read again by the browser; three in a row: browser for a minute", () => {
    const retry = run(on(), [
      [{ type: "enqueue", item: item(1) }, 0],
      [{ type: "failed", id: "c1", source: "server" }, 500],
    ]);
    expect(plays(retry.effects)).toEqual(["c1:server", "c1:browser"]);
    expect(retry.state.counts.fallbacks).toBe(1);

    const noVoice = run(on(false), [
      [{ type: "enqueue", item: item(1) }, 0],
      [{ type: "enqueue", item: item(2) }, 1],
      [{ type: "failed", id: "c1", source: "server" }, 500],
    ]);
    expect(plays(noVoice.effects)).toEqual(["c1:server", "c2:server"]); // the next line instead

    let state = on();
    let now = 0;
    for (let n = 1; n <= 3; n += 1) {
      state = step(state, { type: "enqueue", item: item(n, 0, now) }, now).state;
      state = step(state, { type: "failed", id: `c${n}`, source: "server" }, now + 10).state;
      state = step(state, { type: "ended", id: `c${n}` }, now + 20).state; // the browser read it
      now += 100;
    }
    expect(state.browserUntil).toBe(210 + BROWSER_ONLY_MS);
    const during = run(state, [[{ type: "enqueue", item: item(4, 0, now) }, now]]);
    expect(plays(during.effects)).toEqual(["c4:browser"]);
    const after = run(state, [
      [{ type: "enqueue", item: item(5, 0, now + BROWSER_ONLY_MS) }, now + BROWSER_ONLY_MS],
    ]);
    expect(plays(after.effects)).toEqual(["c5:server"]);

    const reset = run(on(), [
      [{ type: "enqueue", item: item(1) }, 0],
      [{ type: "failed", id: "c1", source: "server" }, 1],
      [{ type: "ended", id: "c1" }, 2],
      [{ type: "enqueue", item: item(2) }, 3],
      [{ type: "ended", id: "c2" }, 4], // a success on the server resets the count
    ]);
    expect(reset.state.serverFailures).toBe(0);

    const stale = run(on(), [
      [{ type: "enqueue", item: item(1, 0, 0) }, 0],
      [{ type: "failed", id: "c1", source: "server" }, (STALE_MS[0] ?? 0) + 1],
    ]);
    expect(plays(stale.effects)).toEqual(["c1:server"]); // too old to read again
  });

  it("R9: paused, the line finishes and no other starts; resume drops the stale", () => {
    const result = run(on(), [
      [{ type: "enqueue", item: item(1, 0, 0) }, 0],
      [{ type: "enqueue", item: item(2, 0, 10) }, 10],
      [{ type: "enqueue", item: item(3, 2, 20) }, 20],
      [{ type: "pause" }, 30],
      [{ type: "ended", id: "c1" }, 1000],
      [{ type: "enqueue", item: item(4, 3, 7000) }, 7000], // a goal while paused: waits
    ]);
    expect(plays(result.effects)).toEqual(["c1:server"]);
    const resumed = run(result.state, [[{ type: "resume" }, 7000]]);
    expect(plays(resumed.effects)).toEqual(["c4:server"]); // c2 is stale (6 s), the goal first
    expect(resumed.state.waiting.map((i) => i.id)).toEqual(["c3"]);
  });

  it("R10: a recap stops the line and holds the queue until it ends", () => {
    const result = run(on(), [
      [{ type: "enqueue", item: item(1, 1, 0) }, 0],
      [{ type: "enqueue", item: item(2, 1, 10) }, 10],
      [{ type: "recapStart" }, 20],
      [{ type: "enqueue", item: item(3, 3, 2000) }, 2000],
    ]);
    expect(result.effects.slice(1)).toEqual([{ type: "stop", id: "c1" }]);
    expect(result.state.current).toBeNull();
    const ended = run(result.state, [[{ type: "recapEnd" }, 3000]]);
    expect(plays(ended.effects)).toEqual(["c3:server"]);
  });

  it("R11: mute stops and forgets; clear does too but stays on; reset keeps the line read", () => {
    const base: [Input, number][] = [
      [{ type: "enqueue", item: item(1) }, 0],
      [{ type: "enqueue", item: item(2) }, 1],
    ];
    const muted = run(on(), [...base, [{ type: "mute" }, 2]]);
    expect(muted.effects.at(-1)).toEqual({ type: "stop", id: "c1" });
    expect(muted.state).toMatchObject({ muted: true, current: null, waiting: [] });

    const cleared = run(on(), [...base, [{ type: "clear" }, 2]]);
    expect(cleared.state).toMatchObject({ muted: false, current: null, waiting: [] });
    expect(plays(run(cleared.state, [[{ type: "enqueue", item: item(3) }, 3]]).effects)).toEqual([
      "c3:server",
    ]);

    const greeting = { ...item(0, 2, 1), id: "greeting-1", greeting: true };
    const reset = run(on(), [
      ...base,
      [{ type: "enqueue", item: greeting }, 1],
      [{ type: "reset" }, 2],
    ]);
    expect(reset.state.current?.item.id).toBe("c1"); // read to the end
    expect(reset.state.waiting.map((i) => i.id)).toEqual(["greeting-1"]); // not a stale line
    expect(reset.effects.filter((e) => e.type === "stop")).toEqual([]);
  });

  it("R12: no browser voice: a line without a clip is dropped and counted", () => {
    const result = run(on(false), [[{ type: "enqueue", item: item(1, 0, 0, false) }, 0]]);
    expect(result.effects).toEqual([]);
    expect(result.state.counts.dropped).toBe(1);
    const later = run(on(true), [
      [{ type: "enqueue", item: item(1, 1, 0) }, 0],
      [{ type: "enqueue", item: item(2, 0, 1, false) }, 1],
      [{ type: "browser", available: false }, 2], // the voice went away while it waited
      [{ type: "ended", id: "c1" }, 3],
    ]);
    expect(plays(later.effects)).toEqual(["c1:server"]);
    expect(later.state.counts.dropped).toBe(1);
  });

  it("skip stops the line and starts the next", () => {
    const result = run(on(), [
      [{ type: "enqueue", item: item(1) }, 0],
      [{ type: "enqueue", item: item(2) }, 1],
      [{ type: "skip" }, 2],
      [{ type: "skip" }, 3],
      [{ type: "skip" }, 4], // nothing left: no effect
    ]);
    expect(
      result.effects.map((e) => (e.type === "play" ? `play ${e.item.id}` : `stop ${e.id}`)),
    ).toEqual(["play c1", "stop c1", "play c2", "stop c2"]);
    expect(result.state.counts.skipped).toBe(2);
  });
});

// ------------------------------------------------------------------------------------ I1-I8

type Command =
  | { kind: "enqueue"; excitement: number; audio: boolean; note: "guard" | null }
  | { kind: "endCurrent" }
  | { kind: "failCurrent" }
  | { kind: "endOther" }
  | { kind: "simple"; input: Input };

const commandArb: fc.Arbitrary<Command> = fc.oneof(
  {
    weight: 6,
    arbitrary: fc.record({
      kind: fc.constant("enqueue" as const),
      excitement: fc.integer({ min: 0, max: 3 }),
      audio: fc.boolean(),
      note: fc.constantFrom(null, null, null, "guard" as const),
    }),
  },
  { weight: 4, arbitrary: fc.constant({ kind: "endCurrent" as const }) },
  { weight: 2, arbitrary: fc.constant({ kind: "failCurrent" as const }) },
  { weight: 1, arbitrary: fc.constant({ kind: "endOther" as const }) },
  {
    weight: 3,
    arbitrary: fc
      .constantFrom<Input>(
        { type: "tick" },
        { type: "pause" },
        { type: "resume" },
        { type: "mute" },
        { type: "unmute" },
        { type: "recapStart" },
        { type: "recapEnd" },
        { type: "reset" },
        { type: "clear" },
        { type: "skip" },
        { type: "browser", available: true },
        { type: "browser", available: false },
      )
      .map((input) => ({ kind: "simple" as const, input })),
  },
);

const sequenceArb = fc.record({
  browser: fc.boolean(),
  steps: fc.array(fc.tuple(commandArb, fc.integer({ min: 0, max: 6000 })), { maxLength: 80 }),
});

describe("scheduler invariants (fast-check)", () => {
  it("I1-I8 hold for any sequence of inputs", () => {
    fc.assert(
      fc.property(sequenceArb, ({ browser, steps }) => {
        let state = initialQueue(browser);
        let now = 0;
        let n = 0;
        let sounding: string | null = null; // the line the player is playing (I1)
        const playsOf = new Map<string, string[]>(); // id -> sources played (I3)
        const firstPlays: AudioItem[] = []; // first play of each line, outside R2 (I7)
        const items = new Map<string, AudioItem>();
        for (const [command, delta] of steps) {
          now += delta;
          let input: Input;
          if (command.kind === "enqueue") {
            n += 1;
            const fresh: AudioItem = {
              id: `c${n}`,
              n,
              excitement: command.excitement,
              clockMs: n,
              cue:
                command.note === "guard"
                  ? { ...cue(false, "guard"), segments: [] }
                  : cue(command.audio),
              arrivedAt: now,
            };
            items.set(fresh.id, fresh);
            input = { type: "enqueue", item: fresh };
          } else if (command.kind === "endCurrent" || command.kind === "failCurrent") {
            const current = state.current;
            if (!current) continue;
            input =
              command.kind === "endCurrent"
                ? { type: "ended", id: current.item.id }
                : { type: "failed", id: current.item.id, source: current.source };
          } else if (command.kind === "endOther") {
            input = { type: "ended", id: "c-unknown" };
          } else {
            input = command.input;
          }
          const before = state;
          const { state: after, effects } = step(before, input, now);

          // the player's view of the effects
          if (input.type === "ended" || input.type === "failed") {
            if (before.current?.item.id === input.id) sounding = null;
          }
          for (const effect of effects) {
            if (effect.type === "stop") {
              expect(effect.id).toBe(sounding); // only the line that sounds is stopped
              sounding = null;
              continue;
            }
            // I1: one voice at a time
            expect(sounding, "two voices at once").toBeNull();
            sounding = effect.item.id;
            // I4: never while muted
            expect(after.muted).toBe(false);
            // I6: never a stale line
            const limit = STALE_MS[Math.min(3, effect.item.excitement)] ?? 0;
            expect(now - effect.item.arrivedAt).toBeLessThanOrEqual(limit);
            // I8: paused (or in a recap), only the line being read may go on (browser retry)
            const retry =
              input.type === "failed" && input.id === effect.item.id && effect.source === "browser";
            if (after.paused || after.recap) expect(retry).toBe(true);
            // I3: once, or the server then the browser after a failure
            const sources = [...(playsOf.get(effect.item.id) ?? []), effect.source];
            playsOf.set(effect.item.id, sources);
            if (sources.length > 1) {
              expect(sources).toEqual(["server", "browser"]);
              expect(retry).toBe(true);
            } else {
              const preempt = effects.some((e) => e.type === "stop") && input.type === "enqueue";
              if (!preempt) firstPlays.push(effect.item);
            }
            // R12: the browser only when the browser has a voice
            if (effect.source === "browser") expect(after.browserAvailable).toBe(true);
          }
          // I2: at most three waiting
          expect(after.waiting.length).toBeLessThanOrEqual(MAX_WAITING);
          // I5: a fresh, playable goal is dropped for room only in favour of other goals
          if (input.type === "enqueue" && !before.muted) {
            const survivors = new Set([
              ...after.waiting.map((i) => i.id),
              ...(after.current ? [after.current.item.id] : []),
            ]);
            const playable = (i: AudioItem) => Boolean(i.cue.audio) || after.browserAvailable;
            const fresh = (i: AudioItem) =>
              now - i.arrivedAt <= (STALE_MS[Math.min(3, i.excitement)] ?? 0);
            const candidates = [
              ...before.waiting,
              ...(input.item.cue.note === "guard" ? [] : [input.item]),
            ];
            const lostGoal = candidates.some(
              (i) => i.excitement >= 3 && fresh(i) && playable(i) && !survivors.has(i.id),
            );
            if (lostGoal) expect(after.waiting.every((i) => i.excitement >= 3)).toBe(true);
          }
          state = after;
        }
        // I7: arrival order, except goals (R5) and cuts (R2)
        const quiet = firstPlays.filter((i) => i.excitement < 3).map((i) => i.n);
        expect(quiet).toEqual([...quiet].sort((a, b) => a - b));
        for (const id of playsOf.keys()) expect(items.has(id)).toBe(true);
      }),
      { numRuns: 400 },
    );
  });
});
