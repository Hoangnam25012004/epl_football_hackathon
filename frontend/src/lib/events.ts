// Event and moment presentation: icons, the "key events" filter (docs/day4-design.md 5.3).
import type { Moment } from "./contracts";
import type { MatchEvent } from "./ws-types";

export type EventKind = NonNullable<MatchEvent["type"]>;

export const EVENT_ICONS: Record<string, string> = {
  match_start: "⏱",
  period_start: "▶",
  period_end: "⏸",
  match_end: "⏹",
  pass: "→",
  shot: "◎",
  goal: "⚽",
  pressure: "↯",
  tackle: "✕",
  interception: "⤫",
  ball_recovery: "↺",
  foul: "!",
  card: "▮",
  offside: "⚑",
  ball_dead: "·",
  substitution: "⇄",
  var_check: "▣",
  player_physical: "·",
};

const KEY_TYPES = new Set([
  "goal",
  "shot",
  "card",
  "substitution",
  "var_check",
  "offside",
  "period_start",
  "period_end",
  "match_end",
]);

export function eventType(event: MatchEvent): string {
  return event.type ?? "event";
}

/** Goals, shots, cards, VAR, offsides, substitutions, corners and the whistle. */
export function isKeyEvent(event: MatchEvent): boolean {
  const type = eventType(event);
  if (KEY_TYPES.has(type)) return true;
  return type === "pass" && "play_type" in event && event.play_type === "corner";
}

export const MOMENT_MARKERS: ReadonlySet<Moment["kind"]> = new Set([
  "goal",
  "goal_overturned",
  "red_card",
]);

/** "goal" / "saved" / "yellow"... for the feed line, when the event has one. */
export function eventDetail(event: MatchEvent): string | null {
  if ("outcome" in event && typeof event.outcome === "string") return event.outcome;
  if ("color" in event && typeof event.color === "string") return event.color;
  if ("decision" in event && typeof event.decision === "string") return event.decision;
  return null;
}
