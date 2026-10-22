// MatchSocket: one WebSocket to /ws/match/{id} with a viewer profile (docs/day4-design.md 4.4).
//
// The server numbers every message (`n`). A duplicate is dropped; a gap means a message was
// lost, so the socket reconnects and the new `hello` replaces the client's state. Unknown
// message types are ignored. Close codes: 4404 unknown match (fatal, or "waiting" for the
// overlay, which OBS may open before the studio creates the match), 4422 bad profile (fatal),
// 4429 too many viewers (retry in 10 s); anything else reconnects with backoff.
import { wsUrl } from "./api";
import { isKnownType, type AnyWsMessage } from "./ws-types";

export type SocketStatus =
  "idle" | "connecting" | "open" | "reconnecting" | "waiting" | "fatal" | "closed";

export type SocketState = {
  status: SocketStatus;
  code?: number;
  reason?: string;
  retryInMs?: number;
  retryAt?: number; // Date.now() of the next attempt
};

export type SocketStats = {
  gaps: number;
  duplicates: number;
  reconnects: number;
  badFrames: number;
  unknownTypes: number;
};

type Timer = ReturnType<typeof setTimeout>;

export type SocketOptions = {
  matchId: string;
  query?: string;
  onMessage: (message: AnyWsMessage) => void;
  onState?: (state: SocketState) => void;
  /** What an unknown match means: "fatal" (studio, fan) or "wait" and retry (overlay). */
  unknownMatch?: "fatal" | "wait";
  /** Dependencies, injected by tests. */
  WebSocketImpl?: typeof WebSocket;
  url?: (path: string) => string;
  random?: () => number;
  setTimer?: (fn: () => void, ms: number) => Timer;
  clearTimer?: (timer: Timer) => void;
  idleMs?: number;
};

export const UNKNOWN_MATCH = 4404;
export const BAD_PROFILE = 4422;
export const TOO_MANY = 4429;
export const MAX_BACKOFF_MS = 8_000;
export const TOO_MANY_RETRY_MS = 10_000;
export const WAIT_RETRY_MS = 5_000;
export const RESTART_RECONNECT_MS = 300;
export const IDLE_MS = 45_000;

export function backoffMs(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt);
  const jitter = 1 + (random() * 0.4 - 0.2); // ±20 %
  return Math.round(Math.min(MAX_BACKOFF_MS, base * jitter));
}

export class MatchSocket {
  readonly stats: SocketStats = {
    gaps: 0,
    duplicates: 0,
    reconnects: 0,
    badFrames: 0,
    unknownTypes: 0,
  };
  private ws: WebSocket | null = null;
  private generation = 0;
  private expectedN: number | null = null; // next n; null until a hello
  private attempts = 0;
  private retryTimer: Timer | null = null;
  private idleTimer: Timer | null = null;
  private stopped = true;
  private matchRunning = false;
  private readonly options: Required<Omit<SocketOptions, "query" | "onState" | "WebSocketImpl">> &
    Pick<SocketOptions, "query" | "onState" | "WebSocketImpl">;

  constructor(options: SocketOptions) {
    this.options = {
      unknownMatch: "fatal",
      url: wsUrl,
      random: Math.random,
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (timer) => clearTimeout(timer),
      idleMs: IDLE_MS,
      ...options,
    };
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  /** Close for good (component unmounted, profile changed). Idempotent. */
  stop(): void {
    this.stopped = true;
    this.generation += 1; // late callbacks of the old socket are ignored
    this.clearTimers();
    const ws = this.ws;
    this.ws = null;
    if (ws && ws.readyState <= 1) ws.close(1000, "client closed");
    this.emitState({ status: "closed" });
  }

  private connect(): void {
    if (this.stopped) return;
    this.clearTimers();
    const old = this.ws;
    this.generation += 1;
    const generation = this.generation;
    if (old && old.readyState <= 1) old.close(1000, "reconnect");
    this.expectedN = null;
    this.emitState({ status: "connecting" });
    const path = `/ws/match/${encodeURIComponent(this.options.matchId)}${
      this.options.query ? `?${this.options.query}` : ""
    }`;
    const Impl = this.options.WebSocketImpl ?? WebSocket;
    let ws: WebSocket;
    try {
      ws = new Impl(this.options.url(path));
    } catch {
      this.scheduleRetry(backoffMs(this.attempts++, this.options.random), "reconnecting");
      return;
    }
    this.ws = ws;
    ws.onmessage = (event: MessageEvent) => this.handle(generation, event.data);
    ws.onclose = (event: CloseEvent) => this.closed(generation, event.code, event.reason);
    ws.onerror = () => {
      /* a close event follows */
    };
  }

  private handle(generation: number, data: unknown): void {
    if (generation !== this.generation || this.stopped) return;
    let message: AnyWsMessage;
    try {
      message = JSON.parse(String(data)) as AnyWsMessage;
    } catch {
      this.stats.badFrames += 1;
      return;
    }
    if (
      message === null ||
      typeof message !== "object" ||
      typeof message.type !== "string" ||
      typeof message.n !== "number"
    ) {
      this.stats.badFrames += 1;
      return;
    }
    this.touch();
    if (message.type === "hello" || message.type === "resync") {
      this.expectedN = message.n + 1;
      this.attempts = 0;
      this.matchRunning = message.payload.match.status === "running";
      this.emitState({ status: "open" });
      this.options.onMessage(message);
      return;
    }
    if (this.expectedN === null) return; // nothing before the hello
    if (message.n < this.expectedN) {
      this.stats.duplicates += 1;
      return;
    }
    if (message.n > this.expectedN) {
      this.stats.gaps += 1;
      this.reconnectSoon(0);
      return;
    }
    this.expectedN += 1;
    if (!isKnownType(message.type)) {
      this.stats.unknownTypes += 1;
      return;
    }
    if (message.type === "status") {
      this.matchRunning = message.payload.session === "running";
      this.options.onMessage(message);
      if (message.payload.restarted) this.reconnectSoon(RESTART_RECONNECT_MS);
      return;
    }
    this.options.onMessage(message);
  }

  private closed(generation: number, code: number, reason: string): void {
    if (generation !== this.generation) return;
    this.ws = null;
    this.clearIdle();
    if (this.stopped) return;
    if (code === UNKNOWN_MATCH) {
      if (this.options.unknownMatch === "wait") {
        this.scheduleRetry(WAIT_RETRY_MS, "waiting", code, reason);
      } else {
        this.emitState({ status: "fatal", code, reason });
      }
      return;
    }
    if (code === BAD_PROFILE) {
      this.emitState({ status: "fatal", code, reason });
      return;
    }
    if (code === TOO_MANY) {
      this.scheduleRetry(TOO_MANY_RETRY_MS, "reconnecting", code, reason);
      return;
    }
    this.scheduleRetry(backoffMs(this.attempts++, this.options.random), "reconnecting", code);
  }

  private reconnectSoon(ms: number): void {
    this.clearTimers();
    this.stats.reconnects += 1;
    if (ms <= 0) {
      this.connect();
      return;
    }
    this.retryTimer = this.options.setTimer(() => this.connect(), ms);
  }

  private scheduleRetry(
    ms: number,
    status: "reconnecting" | "waiting",
    code?: number,
    reason?: string,
  ): void {
    this.clearTimers();
    this.stats.reconnects += 1;
    this.emitState({ status, code, reason, retryInMs: ms, retryAt: Date.now() + ms });
    this.retryTimer = this.options.setTimer(() => this.connect(), ms);
  }

  /** A running match that goes silent for `idleMs` is a dead connection. */
  private touch(): void {
    this.clearIdle();
    this.idleTimer = this.options.setTimer(() => {
      if (this.matchRunning && !this.stopped) this.reconnectSoon(0);
    }, this.options.idleMs);
  }

  private clearIdle(): void {
    if (this.idleTimer !== null) this.options.clearTimer(this.idleTimer);
    this.idleTimer = null;
  }

  private clearTimers(): void {
    if (this.retryTimer !== null) this.options.clearTimer(this.retryTimer);
    this.retryTimer = null;
    this.clearIdle();
  }

  private emitState(state: SocketState): void {
    this.options.onState?.(state);
  }
}
