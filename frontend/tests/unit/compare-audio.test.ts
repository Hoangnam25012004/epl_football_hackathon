import { describe, expect, it } from "vitest";
import { keepForAudio, MAX_WAITING } from "@/components/fan/AudioQueue";
import { alignByItem, formatSide, observed, parseSide } from "@/lib/compare";
import type { CommentaryView } from "@/lib/contracts";
import { DEFAULT_PROFILE } from "@/lib/profile";
import { applyBatch, initialState, items } from "@/store/match-store";
import { fixture } from "../fixtures";

const analyst = items(applyBatch(initialState, fixture("dominant_loser-42.analyst-en")).commentary);
const casual = items(
  applyBatch(initialState, fixture("dominant_loser-42.casual-vi-beginner-har")).commentary,
);

describe("compare", () => {
  it("aligns the two viewers' versions of the same item, newest first", () => {
    const rows = alignByItem(analyst, casual);
    expect(rows.length).toBe(new Set([...analyst, ...casual].map((c) => c.id)).size);
    const both = rows.filter((row) => row.left && row.right);
    expect(both.length).toBeGreaterThan(30);
    for (const row of both) {
      expect(row.left?.id).toBe(row.right?.id);
      expect(row.left?.display_minute).toBe(row.right?.display_minute);
    }
    const orders = rows.map((row) => row.order);
    expect(orders).toEqual([...orders].sort((a, b) => b - a));
    expect(alignByItem(analyst.slice(0, 3), [])[0]?.right).toBeUndefined();
  });

  it("the analyst cites numbers, the beginner gets rules", () => {
    const left = analyst.map(observed);
    const right = casual.map(observed);
    const sum = (list: { numbers: number }[]) => list.reduce((n, x) => n + x.numbers, 0);
    expect(sum(left)).toBeGreaterThan(sum(right));
    expect(right.some((x) => x.rule)).toBe(true);
    expect(left.some((x) => x.rule)).toBe(false);
  });

  it("profiles round-trip through the URL form", () => {
    const profile = {
      ...DEFAULT_PROFILE,
      mode: "casual" as const,
      beginner: true,
      team: "harbor_lions",
    };
    expect(formatSide(profile)).toBe("casual.vi.beginner.club=harbor_lions");
    expect(parseSide(formatSide(profile), DEFAULT_PROFILE)).toEqual(profile);
    expect(parseSide(null, profile)).toBe(profile);
    expect(parseSide("analyst.en", DEFAULT_PROFILE)).toMatchObject({
      mode: "analyst",
      language: "en",
      beginner: false,
    });
  });
});

describe("audio queue (Day 5 interface)", () => {
  it("keeps the most exciting spoken items, in match order", () => {
    const spoken: CommentaryView[] = analyst.slice(0, 10).map((item, index) => ({
      ...item,
      speak: index % 2 === 0,
      excitement: index % 4,
      clock_ms: index * 1000,
    }));
    const kept = keepForAudio(spoken);
    expect(kept.length).toBeLessThanOrEqual(MAX_WAITING);
    expect(kept.every((item) => item.speak)).toBe(true);
    expect(kept.map((item) => item.clock_ms)).toEqual(
      [...kept.map((i) => i.clock_ms)].sort((a, b) => a - b),
    );
  });
});
