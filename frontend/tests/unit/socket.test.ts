import { describe, expect, it } from "vitest";
import {
  BAD_PROFILE,
  MAX_BACKOFF_MS,
  MatchSocket,
  RESTART_RECONNECT_MS,
  TOO_MANY,
  TOO_MANY_RETRY_MS,
  UNKNOWN_MATCH,
  WAIT_RETRY_MS,
  backoffMs,
  type SocketState,
} from "@/lib/socket";
import type { AnyWsMessage } from "@/lib/ws-types";
import { fixture } from "../fixtures";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closedWith: { code: number; reason: string } | null = null;
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  close(code = 1000, reason = ""): void {
    this.readyState = 3;
    this.closedWith = { code, reason };
  }
  receive(message: unknown): void {
    this.onmessage?.({ data: typeof message === "string" ? message : JSON.stringify(message) });
  }
  serverClose(code: number, reason = ""): void {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
}

type Pending = { fn: () => void; ms: number; id: number };

function setup(options: { unknownMatch?: "fatal" | "wait" } = {}) {
  FakeSocket.instances = [];
  const timers: Pending[] = [];
  let nextId = 1;
  const received: AnyWsMessage[] = [];
  const states: SocketState[] = [];
  const socket = new MatchSocket({
    matchId: "dominant_loser-42",
    query: "mode=analyst&lang=en",
    onMessage: (message) => received.push(message),
    onState: (state) => states.push(state),
    WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
    url: (path) => `ws://test${path}`,
    random: () => 0.5,
    setTimer: (fn, ms) => {
      const id = nextId++;
      timers.push({ fn, ms, id });
      return id as unknown as ReturnType<typeof setTimeout>;
    },
    clearTimer: (timer) => {
      const index = timers.findIndex((t) => t.id === (timer as unknown as number));
      if (index >= 0) timers.splice(index, 1);
    },
    ...options,
  });
  const last = () => FakeSocket.instances[FakeSocket.instances.length - 1] as FakeSocket;
  const fire = (predicate: (t: Pending) => boolean) => {
    const index = timers.findIndex(predicate);
    if (index < 0) throw new Error("no such timer");
    const [timer] = timers.splice(index, 1);
    timer?.fn();
  };
  return { socket, timers, received, states, last, fire };
}

const stream = fixture("dominant_loser-42.analyst-en");
const hello = stream[0] as AnyWsMessage;
const after = stream.slice(1, 40);

describe("MatchSocket", () => {
  it("opens with the profile query and applies the hello then each message in order", () => {
    const { socket, received, states, last } = setup();
    socket.start();
    expect(last().url).toBe("ws://test/ws/match/dominant_loser-42?mode=analyst&lang=en");
    last().receive(hello);
    for (const message of after) last().receive(message);
    expect(received.map((m) => m.n)).toEqual([hello.n, ...after.map((m) => m.n)]);
    expect(states.map((s) => s.status)).toEqual(["connecting", "open"]);
    socket.stop();
  });

  it("drops duplicates and reconnects on a gap", () => {
    const { socket, received, last } = setup();
    socket.start();
    const first = last();
    first.receive(hello);
    first.receive(after[0]);
    first.receive(after[0]); // duplicate
    expect(socket.stats.duplicates).toBe(1);
    first.receive(after[2]); // after[1] lost
    expect(socket.stats.gaps).toBe(1);
    expect(first.closedWith?.code).toBe(1000);
    const second = last();
    expect(second).not.toBe(first);
    second.receive(hello);
    expect(received.filter((m) => m.type === "hello")).toHaveLength(2);
    expect(received.map((m) => m.n)).not.toContain(after[2]?.n);
    socket.stop();
  });

  it("ignores frames before the hello, bad JSON and unknown types", () => {
    const { socket, received, last } = setup();
    socket.start();
    last().receive(after[0]); // before the hello
    last().receive("{not json");
    last().receive({ type: 3 });
    last().receive(hello);
    last().receive({ ...after[0], type: "future_type" });
    last().receive(after[1]);
    expect(received.map((m) => m.type)).toEqual(["hello", after[1]?.type]);
    expect(socket.stats.badFrames).toBe(2);
    expect(socket.stats.unknownTypes).toBe(1);
    socket.stop();
  });

  it("4404 is fatal for the studio and a 5 s wait for the overlay", () => {
    const studio = setup();
    studio.socket.start();
    studio.last().serverClose(UNKNOWN_MATCH, "unknown match");
    expect(studio.states.at(-1)).toMatchObject({ status: "fatal", code: UNKNOWN_MATCH });
    expect(studio.timers).toHaveLength(0);

    const overlay = setup({ unknownMatch: "wait" });
    overlay.socket.start();
    overlay.last().serverClose(UNKNOWN_MATCH);
    expect(overlay.states.at(-1)).toMatchObject({ status: "waiting", retryInMs: WAIT_RETRY_MS });
    overlay.fire((t) => t.ms === WAIT_RETRY_MS);
    expect(FakeSocket.instances).toHaveLength(2); // the overlay connected again
    overlay.socket.stop();
  });

  it("4422 is fatal, 4429 retries in 10 s, other closes back off up to 8 s", () => {
    const bad = setup();
    bad.socket.start();
    bad.last().serverClose(BAD_PROFILE, "unknown club");
    expect(bad.states.at(-1)).toMatchObject({ status: "fatal", code: BAD_PROFILE });

    const busy = setup();
    busy.socket.start();
    busy.last().serverClose(TOO_MANY);
    expect(busy.states.at(-1)).toMatchObject({
      status: "reconnecting",
      retryInMs: TOO_MANY_RETRY_MS,
    });

    const flaky = setup();
    flaky.socket.start();
    const delays: number[] = [];
    for (let i = 0; i < 6; i += 1) {
      flaky.last().serverClose(1006);
      const state = flaky.states.at(-1);
      delays.push(state?.retryInMs ?? -1);
      flaky.fire(() => true);
    }
    expect(delays).toEqual([500, 1000, 2000, 4000, 8000, 8000]);
    flaky.socket.stop();
  });

  it("a hello resets the backoff", () => {
    const { socket, states, last, fire } = setup();
    socket.start();
    last().serverClose(1006);
    fire(() => true);
    last().serverClose(1006);
    expect(states.at(-1)?.retryInMs).toBe(1000);
    fire(() => true);
    last().receive(hello);
    last().serverClose(1006);
    expect(states.at(-1)?.retryInMs).toBe(500);
    socket.stop();
  });

  it("reconnects 300 ms after a restart notice", () => {
    const { socket, timers, last, fire } = setup();
    socket.start();
    last().receive(hello);
    const status = { ...after.find((m) => m.type === "status") } as AnyWsMessage;
    const restarted = {
      ...status,
      n: (hello.n as number) + 1,
      payload: { ...(status.payload as object), restarted: true },
    };
    last().receive(restarted);
    expect(timers.some((t) => t.ms === RESTART_RECONNECT_MS)).toBe(true);
    const before = FakeSocket.instances.length;
    fire((t) => t.ms === RESTART_RECONNECT_MS);
    expect(FakeSocket.instances.length).toBe(before + 1);
    socket.stop();
  });

  it("a silent running match is reconnected by the watchdog; stop ignores late callbacks", () => {
    const { socket, timers, received, last, fire } = setup();
    socket.start();
    const first = last();
    const running = { ...hello, payload: { ...(hello.payload as object) } } as AnyWsMessage;
    (running.payload as { match: { status: string } }).match = {
      ...(hello.payload as { match: object }).match,
      status: "running",
    } as never;
    first.receive(running);
    expect(timers.some((t) => t.ms === 45_000)).toBe(true);
    fire((t) => t.ms === 45_000);
    expect(last()).not.toBe(first);
    socket.stop();
    const count = received.length;
    last().receive(hello); // after stop: ignored
    first.serverClose(1006);
    expect(received.length).toBe(count);
  });

  it("start and stop twice (React StrictMode) leave no live socket behind", () => {
    const { socket, last } = setup();
    socket.start();
    socket.stop();
    socket.start();
    const live = last();
    socket.stop();
    expect(live.closedWith?.code).toBe(1000);
    expect(FakeSocket.instances.every((s) => s.readyState === 3)).toBe(true);
  });
});

describe("backoffMs", () => {
  it("doubles from 500 ms with ±20 % jitter and a ceiling of 8 s", () => {
    expect(backoffMs(0, () => 0.5)).toBe(500);
    expect(backoffMs(3, () => 0.5)).toBe(4000);
    expect(backoffMs(10, () => 1)).toBe(MAX_BACKOFF_MS);
    expect(backoffMs(1, () => 0)).toBe(800);
    expect(backoffMs(1, () => 1)).toBe(1200);
  });
});
