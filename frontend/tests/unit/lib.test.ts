import { describe, expect, it, vi } from "vitest";
import { detailOf, parseMatchId, request, wsUrl } from "@/lib/api";
import { formatMinute } from "@/lib/clock";
import { contrastRatio, safeColor, textOn } from "@/lib/color";
import { isKeyEvent } from "@/lib/events";
import { en } from "@/lib/i18n/en";
import { translate } from "@/lib/i18n";
import { vi as viDict } from "@/lib/i18n/vi";
import { PITCH_LENGTH, PITCH_WIDTH, toPitch } from "@/lib/pitch";
import {
  DEFAULT_PROFILE,
  coerceProfile,
  fitProfile,
  loadProfile,
  profileQuery,
  saveProfile,
} from "@/lib/profile";
import { WS_TYPES_COMPLETE } from "@/lib/ws-types";
import { fixture, ofType } from "../fixtures";

describe("formatMinute (golden: every display minute the server sent)", () => {
  it("matches the server for every moment and commentary item", () => {
    const stream = fixture("dominant_loser-42.analyst-en");
    const timed = [
      ...ofType(stream, "moment").map((m) => m.payload),
      ...ofType(stream, "commentary").map((m) => m.payload),
    ];
    expect(timed.length).toBeGreaterThan(50);
    for (const item of timed) {
      expect(formatMinute(item.period, item.clock_ms), item.id).toBe(item.display_minute);
    }
  });

  it("shows stoppage time", () => {
    expect(formatMinute(1, 0)).toBe("1'");
    expect(formatMinute(1, 45 * 60_000)).toBe("45+1'");
    expect(formatMinute(2, 0)).toBe("46'");
    expect(formatMinute(2, 47 * 60_000)).toBe("90+3'");
  });
});

describe("pitch", () => {
  it("draws the home team as is and rotates the away team", () => {
    expect(toPitch({ x: 97, y: 37.7, team: "home" }, "home")).toEqual({ x: 97, y: 37.7 });
    expect(toPitch({ x: 97, y: 30, team: "away" }, "home")).toEqual({
      x: PITCH_LENGTH - 97,
      y: PITCH_WIDTH - 30,
    });
    expect(toPitch({ x: 200, y: -5, team: "home" }, "home")).toEqual({ x: PITCH_LENGTH, y: 0 });
    expect(toPitch({ x: null, y: 3 }, "home")).toBeNull();
  });
});

describe("colour", () => {
  it("picks black or white text with enough contrast", () => {
    for (const background of ["#FFFFFF", "#000000", "#C8102E", "#F6EB61", "#1F3A93", "#7FD4F0"]) {
      expect(contrastRatio(background, textOn(background))).toBeGreaterThanOrEqual(4.5);
    }
    expect(safeColor("nope")).toBe("#64748b");
    expect(safeColor("#ABCDEF")).toBe("#ABCDEF");
  });
});

describe("profile", () => {
  it("round-trips to the query parse_profile reads", () => {
    const query = profileQuery({
      mode: "casual",
      language: "vi",
      team: "harbor_lions",
      player: "harbor_lions-09",
      beginner: true,
      metric: "xg",
    });
    expect(Object.fromEntries(new URLSearchParams(query))).toEqual({
      mode: "casual",
      lang: "vi",
      club: "harbor_lions",
      player: "harbor_lions-09",
      beginner: "1",
      metric: "xg",
    });
    expect(profileQuery(DEFAULT_PROFILE)).toBe("mode=casual&lang=vi");
  });

  it("repairs anything stored badly and drops a club of another match", () => {
    expect(coerceProfile({ mode: "x", language: 3, team: "<script>", beginner: "yes" })).toEqual(
      DEFAULT_PROFILE,
    );
    const fitted = fitProfile(
      { ...DEFAULT_PROFILE, team: "ironvale_united", player: "x-1" },
      ["harbor_lions", "kestrel_athletic"],
      ["harbor_lions-09"],
    );
    expect(fitted.team).toBeNull();
    expect(fitted.player).toBeNull();
  });

  it("survives a storage that throws", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(loadProfile(broken)).toBeNull();
    expect(() => saveProfile(DEFAULT_PROFILE, broken)).not.toThrow();
    saveProfile({ ...DEFAULT_PROFILE, mode: "analyst" });
    expect(loadProfile()?.mode).toBe("analyst");
  });
});

describe("i18n", () => {
  it("Vietnamese has every key, nothing empty, the same placeholders", () => {
    const vars = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(viDict[key], key).toBeTruthy();
      expect(vars(viDict[key]), key).toEqual(vars(en[key]));
    }
    expect(translate("vi", "conn.reconnecting", { s: 4 })).toBe(
      "Mất kết nối. Kết nối lại sau 4 giây…",
    );
    expect(translate("en", "verdict.PARTLY_TRUE")).toBe("Partly true");
  });
});

describe("api", () => {
  it("never throws: errors, timeouts and FastAPI details become results", async () => {
    const respond = (status: number, body: unknown) =>
      vi.fn(async () => new Response(JSON.stringify(body), { status }));
    const notFound = await request("GET", "/x", {
      fetchImpl: respond(404, { detail: "unknown match" }),
    });
    expect(notFound).toEqual({
      ok: false,
      error: { status: 404, code: "not_found", message: "unknown match" },
    });
    const invalid = await request("POST", "/x", {
      body: {},
      fetchImpl: respond(422, { detail: [{ msg: "too short" }, { msg: "bad" }] }),
    });
    expect(invalid.ok === false && invalid.error).toMatchObject({
      code: "invalid",
      message: "too short; bad",
    });
    const conflict = await request("GET", "/x", {
      fetchImpl: respond(409, { detail: "recap_not_ready" }),
    });
    expect(conflict.ok === false && conflict.error.code).toBe("conflict");
    const down = await request("GET", "/x", {
      fetchImpl: vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    });
    expect(down.ok === false && down.error.code).toBe("network");
    const slow = await request("GET", "/x", {
      timeoutMs: 10,
      fetchImpl: (_input, init) =>
        new Promise((_resolve, reject) =>
          init?.signal?.addEventListener("abort", () => reject(new Error("aborted"))),
        ),
    });
    expect(slow.ok === false && slow.error.code).toBe("timeout");
    const fine = await request<{ a: number }>("GET", "/x", { fetchImpl: respond(200, { a: 1 }) });
    expect(fine).toEqual({ ok: true, data: { a: 1 } });
    expect(detailOf(null)).toBeNull();
  });

  it("parses match ids and builds WebSocket URLs", () => {
    expect(parseMatchId("dominant_loser-42")).toEqual({ scenario: "dominant_loser", seed: 42 });
    expect(parseMatchId("dominant_loser-x")).toBeNull();
    expect(parseMatchId("other-1")).toBeNull();
    expect(wsUrl("/ws/match/a", "http://localhost:8000")).toBe("ws://localhost:8000/ws/match/a");
    expect(wsUrl("/ws/match/a", "https://demo.example")).toBe("wss://demo.example/ws/match/a");
    expect(wsUrl("/ws/match/a", "")).toBe(`ws://${window.location.host}/ws/match/a`);
  });
});

describe("events and types", () => {
  it("key events: goals, shots, cards, VAR, corners", () => {
    const events = ofType(fixture("dominant_loser-42.analyst-en"), "event").map((m) => m.payload);
    const key = events.filter(isKeyEvent);
    expect(key.length).toBeGreaterThan(20);
    expect(key.length).toBeLessThan(events.length / 3);
    expect(key.some((e) => e.type === "goal")).toBe(true);
    expect(WS_TYPES_COMPLETE).toBe(true);
  });
});
