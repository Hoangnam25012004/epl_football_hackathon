// "My matches" (docs/day6-design.md section 6.5): the private sessions this browser started,
// kept in localStorage when the browser allows it. A blocked or broken storage only forgets.
import { parseMatchId } from "./api";
import { safeStorage } from "./profile";

export const MY_SESSIONS_KEY = "pundit.my-sessions";
export const MAX_MY_SESSIONS = 10;

export type MySession = { id: string; created: number };

function valid(entry: unknown): entry is MySession {
  if (entry === null || typeof entry !== "object") return false;
  const { id, created } = entry as { id: unknown; created: unknown };
  return typeof id === "string" && parseMatchId(id)?.token != null && typeof created === "number";
}

/** The remembered sessions, newest first. */
export function mySessions(storage: Pick<Storage, "getItem"> | null = safeStorage()): MySession[] {
  try {
    const raw = storage?.getItem(MY_SESSIONS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(valid).slice(0, MAX_MY_SESSIONS) : [];
  } catch {
    return [];
  }
}

function save(sessions: MySession[], storage: Pick<Storage, "setItem"> | null): void {
  try {
    storage?.setItem(MY_SESSIONS_KEY, JSON.stringify(sessions.slice(0, MAX_MY_SESSIONS)));
  } catch {
    /* storage blocked or full: the list lives for this page only */
  }
}

/** Remember a private session (a shared match id is never kept). */
export function rememberSession(
  id: string,
  now: number = Date.now(),
  storage: Pick<Storage, "getItem" | "setItem"> | null = safeStorage(),
): MySession[] {
  if (parseMatchId(id)?.token == null) return mySessions(storage);
  const sessions = [{ id, created: now }, ...mySessions(storage).filter((s) => s.id !== id)];
  save(sessions, storage);
  return sessions.slice(0, MAX_MY_SESSIONS);
}

/** Forget a session (the server removed it). */
export function forgetSession(
  id: string,
  storage: Pick<Storage, "getItem" | "setItem"> | null = safeStorage(),
): MySession[] {
  const sessions = mySessions(storage).filter((s) => s.id !== id);
  save(sessions, storage);
  return sessions;
}
