// Two viewers side by side (docs/day4-design.md section 8). The same commentary item has the same
// id on both connections (one message number, one payload per profile), so rows align by id.
import type { CommentaryView } from "./contracts";
import type { ViewerProfile } from "./profile";

export type Row = {
  id: string;
  minute: string;
  order: number; // match time, for sorting
  left?: CommentaryView;
  right?: CommentaryView;
};

export function alignByItem(
  left: readonly CommentaryView[],
  right: readonly CommentaryView[],
): Row[] {
  const rows = new Map<string, Row>();
  const add = (item: CommentaryView, side: "left" | "right") => {
    const row = rows.get(item.id) ?? {
      id: item.id,
      minute: item.display_minute,
      order: item.period * 10_000_000 + item.clock_ms,
    };
    row[side] = item;
    rows.set(item.id, row);
  };
  for (const item of left) add(item, "left");
  for (const item of right) add(item, "right");
  return [...rows.values()].sort((a, b) => b.order - a.order || b.id.localeCompare(a.id));
}

/** What a reader can see differs between two versions: numbers, a rule explained, length. */
export function observed(item: CommentaryView): { numbers: number; rule: boolean; words: number } {
  const numbers = (item.text.match(/\d+(?:[.,]\d+)?%?/g) ?? []).length + (item.facts?.length ?? 0);
  return {
    numbers,
    rule: Boolean(item.explanation),
    words: item.text.trim().split(/\s+/).filter(Boolean).length,
  };
}

/** "analyst.en", "casual.vi.beginner", "casual.vi.club=harbor_lions" ↔ a profile. */
export function parseSide(text: string | null, fallback: ViewerProfile): ViewerProfile {
  if (!text) return fallback;
  const profile: ViewerProfile = { ...fallback, beginner: false, team: null };
  for (const token of text.split(".")) {
    if (token === "analyst" || token === "casual") profile.mode = token;
    else if (token === "vi" || token === "en") profile.language = token;
    else if (token === "beginner") profile.beginner = true;
    else if (token.startsWith("club=")) profile.team = token.slice(5) || null;
  }
  return profile;
}

export function formatSide(profile: ViewerProfile): string {
  return [
    profile.mode,
    profile.language,
    ...(profile.beginner ? ["beginner"] : []),
    ...(profile.team ? [`club=${profile.team}`] : []),
  ].join(".");
}
