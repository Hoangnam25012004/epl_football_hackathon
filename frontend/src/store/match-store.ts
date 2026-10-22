// One store per WebSocket connection: state = reduce(hello + messages) (docs/day4-design.md 4.5).
// `applyBatch` is pure: it copies each container once per batch, then applies every message.
import { createStore, type StoreApi } from "zustand/vanilla";
import type {
  ClaimResult,
  CommentaryView,
  LowerThirdView,
  MatchInfo,
  MatchSnapshot,
  Moment,
  OnAirState,
  Profile,
  RecapView,
  TalkingPoint,
} from "@/lib/contracts";
import type { AnyWsMessage, MatchEvent, SessionStatus } from "@/lib/ws-types";

export const LIMITS = {
  events: 300,
  moments: 200,
  claims: 50,
  talkingPoints: 100,
  lowerThirds: 30,
  commentary: 200,
} as const;

/** Items by id in arrival order, capped (the oldest go first). */
export type Ordered<T> = { order: string[]; byId: Record<string, T> };

export type TalkingPointStatus = TalkingPoint["status"];

export type MatchState = {
  match: MatchInfo | null;
  snapshot: MatchSnapshot | null;
  status: SessionStatus | null;
  speed: number | null;
  degraded: string[];
  profile: Profile | null;
  onAir: OnAirState | null;
  events: MatchEvent[]; // oldest first, at most LIMITS.events
  moments: Moment[];
  claims: Ordered<ClaimResult>;
  talkingPoints: Ordered<TalkingPoint>;
  lowerThirds: Ordered<LowerThirdView>;
  commentary: Ordered<CommentaryView>;
  recaps: { H1?: RecapView; FULL?: RecapView };
  superseded: string[]; // lower-thirds replaced by a newer one (VAR)
  pending: { talkingPoints: Record<string, TalkingPointStatus> };
  lastN: number;
  helloCount: number; // how many hellos this store has seen (a reconnection bumps it)
};

export function emptyOrdered<T>(): Ordered<T> {
  return { order: [], byId: {} };
}

export const initialState: MatchState = {
  match: null,
  snapshot: null,
  status: null,
  speed: null,
  degraded: [],
  profile: null,
  onAir: null,
  events: [],
  moments: [],
  claims: emptyOrdered(),
  talkingPoints: emptyOrdered(),
  lowerThirds: emptyOrdered(),
  commentary: emptyOrdered(),
  recaps: {},
  superseded: [],
  pending: { talkingPoints: {} },
  lastN: 0,
  helloCount: 0,
};

export function ordered<T>(items: readonly T[], key: (item: T) => string, cap: number): Ordered<T> {
  const result = emptyOrdered<T>();
  for (const item of items) upsert(result, key(item), item, cap);
  return result;
}

/** Mutates `target` (a per-batch copy). An existing id keeps its place and gets the new value. */
function upsert<T>(target: Ordered<T>, id: string, item: T, cap: number): void {
  if (!(id in target.byId)) {
    target.order.push(id);
    while (target.order.length > cap) {
      const dropped = target.order.shift();
      if (dropped !== undefined) delete target.byId[dropped];
    }
  }
  target.byId[id] = item;
}

export function items<T>(collection: Ordered<T>): T[] {
  return collection.order.map((id) => collection.byId[id]).filter((x): x is T => x !== undefined);
}

function copyOrdered<T>(source: Ordered<T>): Ordered<T> {
  return { order: [...source.order], byId: { ...source.byId } };
}

function fromHello(
  state: MatchState,
  message: AnyWsMessage & { type: "hello" | "resync" },
): MatchState {
  const payload = message.payload;
  const talkingPoints = ordered(payload.talking_points ?? [], (tp) => tp.id, LIMITS.talkingPoints);
  // an editor's action still waiting for its message survives a reconnection
  const pending: Record<string, TalkingPointStatus> = {};
  for (const [id, status] of Object.entries(state.pending.talkingPoints)) {
    if (talkingPoints.byId[id]?.status !== status) pending[id] = status;
  }
  const lowerThirds = ordered(payload.lower_thirds ?? [], (lt) => lt.id, LIMITS.lowerThirds);
  const recaps: MatchState["recaps"] = {};
  for (const recap of payload.recaps ?? []) recaps[recap.scope] = recap;
  return {
    ...initialState,
    match: payload.match,
    snapshot: payload.snapshot,
    status: payload.match.status,
    speed: payload.match.speed,
    degraded: payload.degraded,
    profile: payload.profile ?? null,
    onAir: payload.on_air ?? null,
    moments: payload.moments.slice(-LIMITS.moments),
    claims: ordered(payload.claims, (c) => c.claim_id, LIMITS.claims),
    talkingPoints,
    lowerThirds,
    commentary: ordered(payload.commentary ?? [], (c) => c.id, LIMITS.commentary),
    recaps,
    superseded: (payload.lower_thirds ?? []).flatMap((lt) => lt.supersedes ?? []),
    pending: { talkingPoints: pending },
    lastN: message.n,
    helloCount: state.helloCount + 1,
  };
}

export function applyBatch(state: MatchState, messages: readonly AnyWsMessage[]): MatchState {
  if (messages.length === 0) return state;
  let next: MatchState = state;
  let copied = false;
  const own = (): MatchState => {
    if (!copied) {
      next = {
        ...next,
        events: [...next.events],
        moments: [...next.moments],
        claims: copyOrdered(next.claims),
        talkingPoints: copyOrdered(next.talkingPoints),
        lowerThirds: copyOrdered(next.lowerThirds),
        commentary: copyOrdered(next.commentary),
        recaps: { ...next.recaps },
        superseded: [...next.superseded],
        pending: { talkingPoints: { ...next.pending.talkingPoints } },
      };
      copied = true;
    }
    return next;
  };
  for (const message of messages) {
    if (message.type === "hello" || message.type === "resync") {
      next = fromHello(next, message);
      copied = false;
      continue;
    }
    const draft = own();
    draft.lastN = Math.max(draft.lastN, message.n);
    switch (message.type) {
      case "event": {
        const event = message.payload;
        if (!draft.events.some((e) => e.id === event.id)) {
          draft.events.push(event);
          if (draft.events.length > LIMITS.events)
            draft.events.splice(0, draft.events.length - LIMITS.events);
        }
        break;
      }
      case "snapshot":
        draft.snapshot = message.payload;
        break;
      case "moment":
        if (!draft.moments.some((m) => m.id === message.payload.id)) {
          draft.moments.push(message.payload);
          if (draft.moments.length > LIMITS.moments) draft.moments.shift();
        }
        break;
      case "claim":
        upsert(draft.claims, message.payload.claim_id, message.payload, LIMITS.claims);
        break;
      case "status":
        draft.status = message.payload.session;
        draft.speed = message.payload.speed;
        draft.degraded = message.payload.degraded;
        if (draft.match)
          draft.match = {
            ...draft.match,
            status: message.payload.session,
            speed: message.payload.speed,
          };
        break;
      case "talking_point":
        upsert(draft.talkingPoints, message.payload.id, message.payload, LIMITS.talkingPoints);
        break;
      case "talking_point_status": {
        const { id, status } = message.payload;
        const found = draft.talkingPoints.byId[id];
        if (found) draft.talkingPoints.byId[id] = { ...found, status };
        delete draft.pending.talkingPoints[id];
        break;
      }
      case "lower_third":
        upsert(draft.lowerThirds, message.payload.id, message.payload, LIMITS.lowerThirds);
        for (const id of message.payload.supersedes ?? []) {
          if (!draft.superseded.includes(id)) draft.superseded.push(id);
        }
        break;
      case "commentary":
        upsert(draft.commentary, message.payload.id, message.payload, LIMITS.commentary);
        break;
      case "recap": {
        const current = draft.recaps[message.payload.scope];
        if (!current || message.payload.revision >= current.revision) {
          draft.recaps[message.payload.scope] = message.payload;
        }
        break;
      }
      case "on_air":
        if (!draft.onAir || message.payload.revision > draft.onAir.revision) {
          draft.onAir = message.payload;
        }
        break;
    }
  }
  return next;
}

export function reduce(state: MatchState, message: AnyWsMessage): MatchState {
  return applyBatch(state, [message]);
}

export type MatchStore = MatchState & {
  apply: (messages: readonly AnyWsMessage[]) => void;
  setPending: (id: string, status: TalkingPointStatus | null) => void;
  reset: () => void;
};

export function createMatchStore(): StoreApi<MatchStore> {
  return createStore<MatchStore>()((set) => ({
    ...initialState,
    apply: (messages) => set((state) => applyBatch(state, messages)),
    setPending: (id, status) =>
      set((state) => {
        const talkingPoints = { ...state.pending.talkingPoints };
        if (status === null) delete talkingPoints[id];
        else talkingPoints[id] = status;
        return { pending: { talkingPoints } };
      }),
    reset: () => set({ ...initialState }),
  }));
}
