// WebSocket messages, typed from the generated payload map (docs/day4-design.md section 4.2).
import type { WsPayloads } from "./contracts";

export type WsType = keyof WsPayloads;

export type WsMessage<T extends WsType = WsType> = {
  v: 1;
  type: T;
  n: number;
  match_id: string;
  payload: WsPayloads[T];
};

/** A message whose `payload` type follows its `type` (a discriminated union). */
export type AnyWsMessage = { [T in WsType]: WsMessage<T> }[WsType];

export type MatchEvent = WsPayloads["event"];
export type HelloPayload = WsPayloads["hello"];
export type SessionStatus = WsPayloads["status"]["session"];

/** Every message type the UI understands; anything else is ignored (forward compatible). */
export const WS_TYPES = [
  "hello",
  "resync",
  "event",
  "snapshot",
  "moment",
  "claim",
  "status",
  "talking_point",
  "talking_point_status",
  "lower_third",
  "commentary",
  "recap",
  "on_air",
] as const satisfies readonly WsType[];

// Compile-time check: WS_TYPES lists every generated message type.
type Missing = Exclude<WsType, (typeof WS_TYPES)[number]>;
export const WS_TYPES_COMPLETE: Missing extends never ? true : false = true;

const KNOWN = new Set<string>(WS_TYPES);

export function isKnownType(type: string): type is WsType {
  return KNOWN.has(type);
}
