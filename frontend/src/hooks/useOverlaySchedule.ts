"use client";

// Drive the pure scheduler from a connection's store: new lower-thirds, editor decisions, speed,
// and timers for the next change (docs/day4-design.md section 6.3). Items already there when a
// hello arrives are history: only what arrives live is queued.
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { LowerThirdView } from "@/lib/contracts";
import {
  addItem,
  applyOnAir,
  emptySchedule,
  markSeen,
  nextWakeup,
  tick,
  type SchedulerState,
} from "@/lib/overlay-scheduler";
import { items } from "@/store/match-store";
import type { MatchConnection } from "./useMatchConnection";

export function useOverlaySchedule(
  connection: MatchConnection,
  matchId: string | null,
  language: "vi" | "en",
): SchedulerState {
  const [state, setState] = useState<SchedulerState>(emptySchedule);
  const stateRef = useRef<SchedulerState>(emptySchedule);
  const fed = useRef<Set<string>>(new Set());
  const hello = useRef<number>(-1);

  useEffect(() => {
    const commit = (next: SchedulerState) => {
      if (next === stateRef.current) return;
      stateRef.current = next;
      setState(next);
    };
    const sync = () => {
      const store = connection.store.getState();
      const now = performance.now();
      const speed = store.speed ?? 1;
      const lowerThirds = items(store.lowerThirds);
      const lookup = (id: string) => store.lowerThirds.byId[id];
      let next = stateRef.current;
      if (store.helloCount !== hello.current) {
        hello.current = store.helloCount;
        fed.current = new Set(lowerThirds.map((lt) => lt.id));
        next = markSeen(emptySchedule, lowerThirds);
        if (store.onAir) next = applyOnAir(next, store.onAir, now, speed, lookup);
      } else {
        for (const lowerThird of lowerThirds) {
          if (fed.current.has(lowerThird.id)) continue;
          fed.current.add(lowerThird.id);
          next = addItem(next, lowerThird, now, speed);
        }
        if (store.onAir && (!next.onAir || store.onAir.revision > next.onAir.revision)) {
          next = applyOnAir(next, store.onAir, now, speed, lookup);
        }
      }
      commit(tick(next, now, speed));
    };
    sync();
    return connection.store.subscribe(sync);
  }, [connection.store]);

  // a taken item this client has not received (it joined later): fetch it
  const waitingFor = state.waitingFor;
  useEffect(() => {
    if (!waitingFor || !matchId) return;
    let alive = true;
    void api.lowerThirds(matchId, language).then((result) => {
      if (!alive || !result.ok) return;
      const found = result.data.find((lt: LowerThirdView) => lt.id === waitingFor);
      if (!found) return;
      const speed = connection.store.getState().speed ?? 1;
      const next = addItem(stateRef.current, found, performance.now(), speed);
      stateRef.current = next;
      setState(next);
    });
    return () => {
      alive = false;
    };
  }, [waitingFor, matchId, language, connection.store]);

  // wake up when the schedule changes by itself (end of an item, end of a gap, preemption)
  useEffect(() => {
    const at = nextWakeup(state);
    if (at === null) return;
    const timer = setTimeout(
      () => {
        const speed = connection.store.getState().speed ?? 1;
        const next = tick(stateRef.current, performance.now(), speed);
        if (next !== stateRef.current) {
          stateRef.current = next;
          setState(next);
        }
      },
      Math.max(0, at - performance.now()) + 5,
    );
    return () => clearTimeout(timer);
  }, [state, connection.store]);

  return state;
}
