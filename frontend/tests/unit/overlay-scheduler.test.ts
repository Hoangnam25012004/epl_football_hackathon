import fc from "fast-check";
import { describe, expect, it } from "vitest";
import type { LowerThirdView, OnAirState } from "@/lib/contracts";
import {
  CLEAR_GAP_MS,
  MAX_QUEUE,
  MIN_SHOW_MS,
  addItem,
  applyOnAir,
  emptySchedule,
  markSeen,
  nextWakeup,
  showTime,
  tick,
  type SchedulerState,
} from "@/lib/overlay-scheduler";
import { fixture, ofType } from "../fixtures";

const real = ofType(fixture("dominant_loser-42.analyst-en"), "lower_third").map((m) => m.payload);

function lt(
  id: string,
  priority: 1 | 2 | 3 = 2,
  extra: Partial<LowerThirdView> = {},
): LowerThirdView {
  const base = real[0]!;
  return {
    ...base,
    id,
    priority,
    duration_ms: 8_000,
    show_at_match_ms: 1000,
    supersedes: [],
    ...extra,
  };
}

function onAir(revision: number, patch: Partial<OnAirState>): OnAirState {
  return {
    mode: "auto",
    item_id: null,
    hold: false,
    revision,
    last_action: null,
    at_match_ms: 0,
    ...patch,
  };
}

const none = () => undefined;

describe("auto mode", () => {
  it("shows one item at a time for clamp(duration / speed, 4 s, duration)", () => {
    expect(showTime(lt("a"), 1)).toBe(8_000);
    expect(showTime(lt("a"), 20)).toBe(MIN_SHOW_MS);
    expect(showTime(lt("a", 2, { duration_ms: 2_000 }), 1)).toBe(MIN_SHOW_MS);
    let state = addItem(emptySchedule, lt("a"), 0, 20);
    state = addItem(state, lt("b"), 100, 20);
    expect(state.current?.item.id).toBe("a");
    expect(state.queue.map((x) => x.id)).toEqual(["b"]);
    expect(nextWakeup(state)).toBe(MIN_SHOW_MS);
    state = tick(state, MIN_SHOW_MS, 20);
    expect(state.current?.item.id).toBe("b");
    state = tick(state, 2 * MIN_SHOW_MS, 20);
    expect(state.current).toBeNull();
  });

  it("a higher priority replaces the current item after 2 s", () => {
    let state = addItem(emptySchedule, lt("low", 1), 0, 1);
    state = addItem(state, lt("goal", 3), 500, 1);
    expect(state.current?.item.id).toBe("low"); // not yet: on for 0.5 s only
    expect(nextWakeup(state)).toBe(2_000);
    state = tick(state, 2_000, 1);
    expect(state.current?.item.id).toBe("goal");
  });

  it("supersedes removes the old item, or replaces it on screen at once", () => {
    let state = addItem(emptySchedule, lt("goal", 3), 0, 1);
    state = addItem(state, lt("other", 1), 10, 1);
    state = addItem(state, lt("var", 3, { supersedes: ["goal"] }), 20, 1);
    expect(state.current?.item.id).toBe("var");
    state = addItem(state, lt("goal", 3), 30, 1); // a late copy of the overturned goal
    expect(state.queue.map((x) => x.id)).toEqual(["other"]);
  });

  it("never shows an item twice and keeps at most five waiting", () => {
    let state = addItem(emptySchedule, lt("a"), 0, 1);
    state = addItem(state, lt("a"), 1, 1);
    for (let i = 0; i < 10; i += 1) state = addItem(state, lt(`q${i}`, i % 2 ? 2 : 1), 2 + i, 1);
    expect(state.queue.length).toBe(MAX_QUEUE);
    expect(state.queue.every((x) => x.priority === 2)).toBe(true); // the lowest dropped first
  });

  it("items present at connection are history, not news", () => {
    const state = addItem(markSeen(emptySchedule, [lt("old")]), lt("old"), 0, 1);
    expect(state.current).toBeNull();
  });
});

describe("editor decisions", () => {
  const lookup = (id: string) => (id === "taken" ? lt("taken") : undefined);

  it("take shows an item with no time limit until clear", () => {
    let state = addItem(emptySchedule, lt("a"), 0, 1);
    state = applyOnAir(
      state,
      onAir(1, { mode: "manual", item_id: "taken", last_action: "take" }),
      100,
      1,
      lookup,
    );
    expect(state.current).toMatchObject({ item: { id: "taken" }, until: null });
    expect(tick(state, 1e9, 1).current?.item.id).toBe("taken");
    expect(nextWakeup(state)).toBeNull();
    state = applyOnAir(
      state,
      onAir(2, { mode: "manual", item_id: null, last_action: "clear" }),
      200,
      1,
      lookup,
    );
    expect(state.current).toBeNull();
  });

  it("a taken item this client does not have shows when it arrives", () => {
    let state = applyOnAir(
      emptySchedule,
      onAir(1, { mode: "manual", item_id: "late" }),
      0,
      1,
      none,
    );
    expect(state.waitingFor).toBe("late");
    state = addItem(state, lt("late"), 50, 1);
    expect(state.current).toMatchObject({ item: { id: "late" }, until: null });
  });

  it("hold freezes the current item; release starts its time again", () => {
    let state = addItem(emptySchedule, lt("a"), 0, 1);
    state = applyOnAir(state, onAir(1, { hold: true, last_action: "hold" }), 1_000, 1, none);
    state = addItem(state, lt("b"), 1_500, 1);
    expect(tick(state, 60_000, 1).current?.item.id).toBe("a");
    state = applyOnAir(state, onAir(2, { hold: false, last_action: "release" }), 60_000, 1, none);
    expect(state.current?.until).toBe(60_000 + 8_000);
  });

  it("clear in auto skips what is showing; auto after a take ends the take", () => {
    let state = addItem(emptySchedule, lt("a"), 0, 1);
    state = addItem(state, lt("b"), 10, 1);
    state = applyOnAir(state, onAir(1, { last_action: "clear" }), 1_000, 1, none);
    expect(state.current).toBeNull();
    state = tick(state, 1_000 + CLEAR_GAP_MS, 1);
    expect(state.current?.item.id).toBe("b");
    state = applyOnAir(state, onAir(2, { mode: "manual", item_id: "taken" }), 2_000, 1, lookup);
    state = applyOnAir(state, onAir(3, { mode: "auto", last_action: "auto" }), 3_000, 1, lookup);
    expect(state.current).toBeNull();
  });

  it("an older decision is ignored", () => {
    const state = applyOnAir(
      emptySchedule,
      onAir(5, { mode: "manual", item_id: "taken" }),
      0,
      1,
      lookup,
    );
    expect(applyOnAir(state, onAir(4, { mode: "auto" }), 1, 1, lookup)).toBe(state);
  });
});

describe("scheduler properties", () => {
  it("never two items, manual never times out, a superseded item never comes back", () => {
    const action = fc.oneof(
      fc.record({
        kind: fc.constant("add" as const),
        id: fc.integer({ min: 0, max: 8 }),
        priority: fc.constantFrom(1, 2, 3) as fc.Arbitrary<1 | 2 | 3>,
        supersede: fc.option(fc.integer({ min: 0, max: 8 })),
      }),
      fc.record({ kind: fc.constant("tick" as const), dt: fc.integer({ min: 0, max: 10_000 }) }),
      fc.record({
        kind: fc.constant("air" as const),
        mode: fc.constantFrom("auto", "manual") as fc.Arbitrary<"auto" | "manual">,
        hold: fc.boolean(),
        id: fc.integer({ min: 0, max: 8 }),
      }),
    );
    fc.assert(
      fc.property(fc.array(action, { maxLength: 60 }), (actions) => {
        let state: SchedulerState = emptySchedule;
        let now = 0;
        let revision = 0;
        const items = new Map<string, LowerThirdView>();
        for (const step of actions) {
          if (step.kind === "add") {
            const item = lt(`i${step.id}`, step.priority, {
              supersedes:
                step.supersede === null
                  ? []
                  : [`i${step.supersede}`].filter((x) => x !== `i${step.id}`),
            });
            items.set(item.id, item);
            state = addItem(state, item, now, 20);
          } else if (step.kind === "tick") {
            now += step.dt;
            state = tick(state, now, 20);
          } else {
            revision += 1;
            state = applyOnAir(
              state,
              onAir(revision, {
                mode: step.mode,
                hold: step.hold,
                item_id: step.mode === "manual" ? `i${step.id}` : null,
              }),
              now,
              20,
              (id) => items.get(id),
            );
          }
          if (state.onAir?.mode === "manual" && state.current)
            expect(state.current.until).toBeNull();
          if (state.current) {
            expect(state.queue.some((q) => q.id === state.current?.item.id)).toBe(false);
          }
          expect(state.queue.length).toBeLessThanOrEqual(MAX_QUEUE);
          for (const queued of state.queue) expect(state.superseded).not.toContain(queued.id);
        }
      }),
      { numRuns: 200 },
    );
  });
});
