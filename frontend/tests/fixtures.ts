// Recorded WebSocket streams (backend/scripts/record_ws.py, docs/day4-design.md section 11.2).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { AnyWsMessage } from "@/lib/ws-types";

const DIR = join(__dirname, "fixtures");

export type FixtureName =
  | "dominant_loser-42.analyst-en"
  | "dominant_loser-42.casual-vi-beginner-har"
  | "dominant_loser-42.late-hello.analyst-en"
  | "dominant_loser-42.llm-long.analyst-en"
  | "dominant_loser-42.llm-long.casual-vi-beginner-har";

const cache = new Map<FixtureName, AnyWsMessage[]>();

export function fixture(name: FixtureName): AnyWsMessage[] {
  let found = cache.get(name);
  if (!found) {
    found = readFileSync(join(DIR, `${name}.jsonl`), "utf8")
      .split("\n")
      .filter((line) => line.trim())
      .map((line) => JSON.parse(line) as AnyWsMessage);
    cache.set(name, found);
  }
  return found;
}

export function ofType<T extends AnyWsMessage["type"]>(
  messages: AnyWsMessage[],
  type: T,
): Extract<AnyWsMessage, { type: T }>[] {
  return messages.filter((m): m is Extract<AnyWsMessage, { type: T }> => m.type === type);
}
