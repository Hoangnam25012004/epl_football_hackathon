import fc from "fast-check";
import { describe, expect, it } from "vitest";
import type { AnyWsMessage } from "@/lib/ws-types";
import {
  LIMITS,
  applyBatch,
  createMatchStore,
  initialState,
  items,
  reduce,
  type MatchState,
} from "@/store/match-store";
import { FrameBatcher } from "@/store/frame-batcher";
import { fixture, ofType } from "../fixtures";

const analyst = fixture("dominant_loser-42.analyst-en");
const casual = fixture("dominant_loser-42.casual-vi-beginner-har");

function replay(messages: readonly AnyWsMessage[], from: MatchState = initialState): MatchState {
  return applyBatch(from, messages);
}

describe("reduce", () => {
  it("a whole recorded match ends in the final state", () => {
    const state = replay(analyst);
    expect(state.status).toBe("finished");
    expect(state.match?.match_id).toBe("dominant_loser-42");
    expect(state.snapshot?.score).toEqual({ harbor_lions: 1, kestrel_athletic: 2 });
    expect(state.events.length).toBe(LIMITS.events);
    expect(state.recaps.H1?.scope).toBe("H1");
    expect(state.recaps.FULL?.scope).toBe("FULL");
    expect(state.onAir?.mode).toBe("auto"); // take then auto in the recording
    expect(items(state.talkingPoints).length).toBeGreaterThan(5);
    expect(items(state.commentary).every((c) => c.language === "en" && c.mode === "analyst")).toBe(
      true,
    );
    expect(state.lastN).toBe(analyst.at(-1)?.n);
  });

  it("one batch equals one message at a time", () => {
    const sliced = analyst.slice(0, 600);
    const oneByOne = sliced.reduce(reduce, initialState);
    expect(replay(sliced)).toEqual(oneByOne);
  });

  it("a hello replaces the state but keeps unconfirmed editor actions", () => {
    const state = replay(analyst.slice(0, 900));
    const tp = items(state.talkingPoints)[0];
    expect(tp).toBeDefined();
    const pending = { ...state, pending: { talkingPoints: { [tp!.id]: "pinned" as const } } };
    const late = fixture("dominant_loser-42.late-hello.analyst-en")[0] as AnyWsMessage;
    const after = reduce(pending, late);
    expect(after.helloCount).toBe(state.helloCount + 1);
    expect(after.pending.talkingPoints[tp!.id]).toBe("pinned");
    expect(after.events).toEqual([]); // the hello carries no events; the feed refills by REST
  });

  it("talking point status confirms the pending action", () => {
    const tp = ofType(analyst, "talking_point")[0]!;
    let state = replay([analyst[0]!, tp]);
    state = { ...state, pending: { talkingPoints: { [tp.payload.id]: "approved" } } };
    state = reduce(state, {
      v: 1,
      type: "talking_point_status",
      n: tp.n + 1,
      match_id: tp.match_id,
      payload: { id: tp.payload.id, status: "approved" },
    });
    expect(state.talkingPoints.byId[tp.payload.id]?.status).toBe("approved");
    expect(state.pending.talkingPoints).toEqual({});
  });

  it("on_air and recaps only move forward", () => {
    const start = replay([analyst[0]!]);
    const onAir = (revision: number, mode: "auto" | "manual"): AnyWsMessage => ({
      v: 1,
      type: "on_air",
      n: revision,
      match_id: "dominant_loser-42",
      payload: { mode, item_id: null, hold: false, revision, last_action: null, at_match_ms: 0 },
    });
    const state = [onAir(2, "manual"), onAir(1, "auto")].reduce(reduce, start);
    expect(state.onAir).toMatchObject({ revision: 2, mode: "manual" });
    const recap = ofType(analyst, "recap").find((r) => r.payload.scope === "FULL")!;
    const older = { ...recap, payload: { ...recap.payload, revision: 0, title: "old" } };
    const kept = [recap, older].reduce(reduce, start);
    expect(kept.recaps.FULL?.title).toBe(recap.payload.title);
  });

  it("each profile gets its own texts for the same items", () => {
    const a = replay(analyst);
    const b = replay(casual);
    const ids = a.commentary.order.filter((id) => id in b.commentary.byId);
    expect(ids.length).toBeGreaterThan(10);
    const differ = ids.filter((id) => a.commentary.byId[id]?.text !== b.commentary.byId[id]?.text);
    expect(differ.length / ids.length).toBeGreaterThan(0.9);
  });
});

describe("reduce properties", () => {
  const body = analyst.slice(1, 400);

  it("delivering a message twice changes nothing", () => {
    fc.assert(
      fc.property(fc.array(fc.nat(body.length - 1), { maxLength: 30 }), (dups) => {
        const doubled: AnyWsMessage[] = [analyst[0]!];
        body.forEach((message, index) => {
          doubled.push(message);
          if (dups.includes(index)) doubled.push(message);
        });
        const once = replay([analyst[0]!, ...body]);
        const twice = replay(doubled);
        expect(items(twice.commentary)).toEqual(items(once.commentary));
        expect(twice.events).toEqual(once.events);
        expect(items(twice.talkingPoints)).toEqual(items(once.talkingPoints));
      }),
      { numRuns: 40 },
    );
  });

  it("unknown types and limits never break the state", () => {
    fc.assert(
      fc.property(fc.nat(analyst.length - 1), (cut) => {
        const state = replay([
          ...analyst.slice(0, cut + 1),
          {
            v: 1,
            type: "future_type",
            n: 999999,
            match_id: "x",
            payload: {},
          } as unknown as AnyWsMessage,
        ]);
        expect(state.events.length).toBeLessThanOrEqual(LIMITS.events);
        expect(state.commentary.order.length).toBeLessThanOrEqual(LIMITS.commentary);
        expect(new Set(state.commentary.order).size).toBe(state.commentary.order.length);
      }),
      { numRuns: 40 },
    );
  });
});

describe("store and batcher", () => {
  it("applies a frame's messages at once and records latencies", () => {
    const store = createMatchStore();
    const frames: (() => void)[] = [];
    let clock = 0;
    const batcher = new FrameBatcher<AnyWsMessage>(
      (messages) => store.getState().apply(messages),
      (callback) => frames.push(callback),
      () => clock,
    );
    let renders = 0;
    store.subscribe(() => (renders += 1));
    for (const message of analyst.slice(0, 50)) batcher.push(message);
    expect(frames).toHaveLength(1);
    clock = 12;
    frames.shift()?.();
    expect(renders).toBe(1);
    expect(store.getState().match?.match_id).toBe("dominant_loser-42");
    expect(batcher.latencies).toHaveLength(50);
    expect(Math.max(...batcher.latencies)).toBe(12);
    store.getState().setPending("x", "pinned");
    expect(store.getState().pending.talkingPoints).toEqual({ x: "pinned" });
    store.getState().setPending("x", null);
    expect(store.getState().pending.talkingPoints).toEqual({});
  });
});
