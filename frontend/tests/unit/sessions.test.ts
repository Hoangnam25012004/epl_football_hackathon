// Day 6 private sessions on the client (docs/day6-design.md section 6.5): session ids, the body
// that makes a session again under its own id, refusals that say when to come back, and the
// "my matches" list in a storage that may be blocked, broken or full.
import { describe, expect, it, vi } from "vitest";
import { isPrivate, parseMatchId, request, sessionBody } from "@/lib/api";
import {
  forgetSession,
  MAX_MY_SESSIONS,
  MY_SESSIONS_KEY,
  mySessions,
  rememberSession,
} from "@/lib/sessions";

class Memory implements Pick<Storage, "getItem" | "setItem"> {
  values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

const blocked: Pick<Storage, "getItem" | "setItem"> = {
  getItem: () => {
    throw new DOMException("blocked", "SecurityError");
  },
  setItem: () => {
    throw new DOMException("full", "QuotaExceededError");
  },
};

describe("session ids", () => {
  it("a private session id carries its token, a shared one none", () => {
    expect(parseMatchId("dominant_loser-42~k3v9x2ma")).toEqual({
      scenario: "dominant_loser",
      seed: 42,
      token: "k3v9x2ma",
    });
    expect(parseMatchId("comeback_var-7")).toEqual({
      scenario: "comeback_var",
      seed: 7,
      token: null,
    });
    for (const bad of ["dominant_loser-42~short", "dominant_loser-42~K3V9X2MA", "x-1~abcdefgh"]) {
      expect(parseMatchId(bad)).toBeNull();
    }
    expect(isPrivate("dominant_loser-42~k3v9x2ma")).toBe(true);
    expect(isPrivate("dominant_loser-42")).toBe(false);
  });

  it("the body that makes a session again keeps its token (never a `token` field)", () => {
    const mine = parseMatchId("balanced-3~a1b2c3d4");
    const shared = parseMatchId("balanced-3");
    expect(mine && sessionBody(mine)).toEqual({
      scenario: "balanced",
      seed: 3,
      session: "a1b2c3d4",
    });
    expect(shared && sessionBody(shared)).toEqual({ scenario: "balanced", seed: 3 });
  });
});

describe("refusals that say when to come back", () => {
  const respond = (status: number, body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status }));

  it("429 and 503 with retry_after_s are busy", async () => {
    const many = await request("POST", "/matches", {
      body: {},
      fetchImpl: respond(429, { detail: "too_many_sessions", retry_after_s: 120 }),
    });
    expect(many.ok === false && many.error).toEqual({
      status: 429,
      code: "busy",
      message: "too_many_sessions",
      retryAfterS: 120,
    });
    const full = await request("POST", "/matches", {
      body: {},
      fetchImpl: respond(503, { detail: "demo_full", retry_after_s: 60 }),
    });
    expect(full.ok === false && full.error).toMatchObject({ code: "busy", retryAfterS: 60 });
  });

  it("a 503 that does not say when stays a server error", async () => {
    const down = await request("GET", "/x", { fetchImpl: respond(503, { detail: "starting" }) });
    expect(down.ok === false && down.error).toEqual({
      status: 503,
      code: "server",
      message: "starting",
    });
  });
});

describe("my matches", () => {
  it("remembers private sessions, newest first, each once", () => {
    const storage = new Memory();
    rememberSession("dominant_loser-42~aaaaaaaa", 1, storage);
    rememberSession("comeback_var-7~bbbbbbbb", 2, storage);
    rememberSession("dominant_loser-42~aaaaaaaa", 3, storage);
    expect(mySessions(storage)).toEqual([
      { id: "dominant_loser-42~aaaaaaaa", created: 3 },
      { id: "comeback_var-7~bbbbbbbb", created: 2 },
    ]);
  });

  it("never keeps a shared match, nor more than the cap", () => {
    const storage = new Memory();
    rememberSession("dominant_loser-42", 1, storage);
    expect(mySessions(storage)).toEqual([]);
    for (let i = 0; i < MAX_MY_SESSIONS + 5; i++) {
      rememberSession(`balanced-${i}~abcdefgh`, i, storage);
    }
    const kept = mySessions(storage);
    expect(kept).toHaveLength(MAX_MY_SESSIONS);
    expect(kept[0]?.id).toBe(`balanced-${MAX_MY_SESSIONS + 4}~abcdefgh`);
  });

  it("forgets a session the server removed", () => {
    const storage = new Memory();
    rememberSession("dominant_loser-42~aaaaaaaa", 1, storage);
    rememberSession("comeback_var-7~bbbbbbbb", 2, storage);
    expect(forgetSession("dominant_loser-42~aaaaaaaa", storage)).toEqual([
      { id: "comeback_var-7~bbbbbbbb", created: 2 },
    ]);
  });

  it("a broken, foreign or blocked storage only forgets", () => {
    const storage = new Memory();
    storage.setItem(MY_SESSIONS_KEY, "{not json");
    expect(mySessions(storage)).toEqual([]);
    storage.setItem(MY_SESSIONS_KEY, JSON.stringify([{ id: 7 }, { id: "x", created: 1 }, null]));
    expect(mySessions(storage)).toEqual([]);
    expect(mySessions(blocked)).toEqual([]);
    expect(rememberSession("dominant_loser-42~aaaaaaaa", 1, blocked)).toEqual([
      { id: "dominant_loser-42~aaaaaaaa", created: 1 },
    ]);
    expect(mySessions(null)).toEqual([]);
  });
});
