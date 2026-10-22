// Which lower-third is on air (docs/day4-design.md section 6.3). Pure functions over a small state,
// shared by the overlay page and the studio monitor: the same messages give the same sequence.
//
// auto:    a queue by priority (high first) then match time; one item at a time, shown for
//          clamp(duration / speed, 4 s, duration); a higher priority item replaces the current
//          one after it has been on for 2 s; `supersedes` removes (or replaces at once) older ones.
// manual:  only the item an editor took, until clear / another take / auto.
// hold:    the current item stays; new ones wait in the queue.
import type { LowerThirdView, OnAirState } from "./contracts";

export const MIN_SHOW_MS = 4_000;
export const PREEMPT_AFTER_MS = 2_000;
export const CLEAR_GAP_MS = 1_000;
export const MAX_QUEUE = 5;
const MAX_SEEN = 400;

export type Shown = { item: LowerThirdView; since: number; until: number | null };

export type SchedulerState = {
  current: Shown | null;
  queue: LowerThirdView[];
  seen: string[]; // ids already queued or shown: never twice
  superseded: string[];
  onAir: OnAirState | null;
  waitingFor: string | null; // a taken item this client does not have yet
  gapUntil: number | null;
};

export const emptySchedule: SchedulerState = {
  current: null,
  queue: [],
  seen: [],
  superseded: [],
  onAir: null,
  waitingFor: null,
  gapUntil: null,
};

export function showTime(item: LowerThirdView, speed: number): number {
  const longest = Math.max(item.duration_ms, MIN_SHOW_MS);
  return Math.round(
    Math.min(longest, Math.max(MIN_SHOW_MS, item.duration_ms / Math.max(speed, 1))),
  );
}

function manual(state: SchedulerState): boolean {
  return state.onAir?.mode === "manual";
}

function holding(state: SchedulerState): boolean {
  return state.onAir?.hold === true;
}

function byPriority(a: LowerThirdView, b: LowerThirdView): number {
  return b.priority - a.priority || a.show_at_match_ms - b.show_at_match_ms;
}

function remember(seen: string[], id: string): string[] {
  const next = seen.includes(id) ? seen : [...seen, id];
  return next.length > MAX_SEEN ? next.slice(next.length - MAX_SEEN) : next;
}

function show(item: LowerThirdView, now: number, timed: boolean, speed: number): Shown {
  return { item, since: now, until: timed ? now + showTime(item, speed) : null };
}

/** Items already there when a client connects are history, not news: never queued. */
export function markSeen(state: SchedulerState, items: readonly LowerThirdView[]): SchedulerState {
  let seen = state.seen;
  const superseded = [...state.superseded];
  for (const item of items) {
    seen = remember(seen, item.id);
    for (const id of item.supersedes ?? []) if (!superseded.includes(id)) superseded.push(id);
  }
  return { ...state, seen, superseded };
}

export function addItem(
  state: SchedulerState,
  item: LowerThirdView,
  now: number,
  speed: number,
): SchedulerState {
  if (state.waitingFor === item.id) {
    return {
      ...state,
      waitingFor: null,
      seen: remember(state.seen, item.id),
      current: show(item, now, false, speed),
    };
  }
  if (state.seen.includes(item.id) || state.superseded.includes(item.id)) return state;
  const replaces = new Set(item.supersedes ?? []);
  const superseded = [
    ...state.superseded,
    ...[...replaces].filter((id) => !state.superseded.includes(id)),
  ];
  let next: SchedulerState = {
    ...state,
    seen: remember(state.seen, item.id),
    superseded,
    queue: state.queue.filter((queued) => !replaces.has(queued.id)),
  };
  // an overturned goal must leave the screen at once, whatever the mode
  if (next.current && replaces.has(next.current.item.id)) {
    return { ...next, current: show(item, now, !manual(next) && !holding(next), speed) };
  }
  next = { ...next, queue: [...next.queue, item].sort(byPriority).slice(0, MAX_QUEUE) };
  return tick(next, now, speed);
}

export function applyOnAir(
  state: SchedulerState,
  onAir: OnAirState,
  now: number,
  speed: number,
  lookup: (id: string) => LowerThirdView | undefined,
): SchedulerState {
  if (state.onAir && onAir.revision <= state.onAir.revision) return state;
  const wasManual = manual(state);
  let next: SchedulerState = { ...state, onAir, waitingFor: null };
  if (onAir.mode === "manual") {
    if (onAir.item_id === null) return { ...next, current: null };
    if (next.current?.item.id === onAir.item_id) {
      return { ...next, current: { ...next.current, until: null } };
    }
    const item = lookup(onAir.item_id);
    if (!item) return { ...next, current: null, waitingFor: onAir.item_id };
    return {
      ...next,
      seen: remember(next.seen, item.id),
      queue: next.queue.filter((queued) => queued.id !== item.id), // taken: never shown again
      current: show(item, now, false, speed),
    };
  }
  // auto
  if (wasManual || onAir.last_action === "clear") {
    next = { ...next, current: null, gapUntil: now + CLEAR_GAP_MS };
  } else if (onAir.hold && next.current) {
    next = { ...next, current: { ...next.current, until: null } };
  } else if (!onAir.hold && next.current && next.current.until === null) {
    next = { ...next, current: show(next.current.item, now, true, speed) };
  }
  return tick(next, now, speed);
}

export function tick(state: SchedulerState, now: number, speed: number): SchedulerState {
  if (manual(state) || holding(state)) return state;
  let next = state;
  if (next.current && next.current.until !== null && now >= next.current.until) {
    next = { ...next, current: null };
  }
  const head = next.queue[0];
  if (
    head &&
    next.current &&
    head.priority > next.current.item.priority &&
    now - next.current.since >= PREEMPT_AFTER_MS
  ) {
    return { ...next, queue: next.queue.slice(1), current: show(head, now, true, speed) };
  }
  if (!next.current && head && (next.gapUntil === null || now >= next.gapUntil)) {
    return {
      ...next,
      queue: next.queue.slice(1),
      gapUntil: null,
      current: show(head, now, true, speed),
    };
  }
  return next;
}

/** When the state changes on its own next (end of an item, end of a gap, a preemption). */
export function nextWakeup(state: SchedulerState): number | null {
  if (manual(state) || holding(state)) return null;
  const times: number[] = [];
  if (state.current?.until != null) times.push(state.current.until);
  if (!state.current && state.queue.length && state.gapUntil !== null) times.push(state.gapUntil);
  const head = state.queue[0];
  if (head && state.current && head.priority > state.current.item.priority) {
    times.push(state.current.since + PREEMPT_AFTER_MS);
  }
  return times.length ? Math.min(...times) : null;
}
