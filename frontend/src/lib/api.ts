// REST client: typed results, timeouts, never throws (docs/day4-design.md section 4.3).
import type {
  ClaimResult,
  CommentaryView,
  CreateMatch,
  EventsPage,
  Health,
  LowerThirdView,
  MatchInfo,
  MatchView,
  OnAirCommand,
  OnAirState,
  PlayerBrief,
  RecapView,
  ScenarioInfo,
  StatsPanel,
  StudioItemDetail,
  TalkingPoint,
  TimelineView,
} from "./contracts";

export type ApiErrorCode =
  "not_found" | "conflict" | "invalid" | "server" | "network" | "timeout" | "other";

export type ApiError = { status: number; code: ApiErrorCode; message: string };
export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

export const DEFAULT_TIMEOUT_MS = 8_000;
export const CLAIM_TIMEOUT_MS = 20_000; // a claim may wait for its LLM budget

/** Empty in the static build (same origin as the API); set by `npm run dev`. */
export function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/+$/, "");
}

export function wsUrl(path: string, base: string = apiBase()): string {
  if (base) return base.replace(/^http/, "ws") + path;
  const { protocol, host } = window.location;
  return `${protocol === "https:" ? "wss" : "ws"}://${host}${path}`;
}

function codeOf(status: number): ApiErrorCode {
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "invalid";
  if (status >= 500) return "server";
  return "other";
}

/** FastAPI's `detail`: a string, or a list of validation errors. */
export function detailOf(body: unknown): string | null {
  if (body === null || typeof body !== "object" || !("detail" in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : ""))
      .filter(Boolean);
    return messages.length ? messages.join("; ") : null;
  }
  return null;
}

type RequestOptions = { body?: unknown; timeoutMs?: number; fetchImpl?: typeof fetch };

export async function request<T>(
  method: "GET" | "POST",
  path: string,
  { body, timeoutMs = DEFAULT_TIMEOUT_MS, fetchImpl = fetch }: RequestOptions = {},
): Promise<Result<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(apiBase() + path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await response.text();
    let json: unknown = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = null;
    }
    if (!response.ok) {
      const message = detailOf(json) ?? (response.statusText || `HTTP ${response.status}`);
      return {
        ok: false,
        error: { status: response.status, code: codeOf(response.status), message },
      };
    }
    return { ok: true, data: json as T };
  } catch (error) {
    const aborted = controller.signal.aborted;
    return {
      ok: false,
      error: {
        status: 0,
        code: aborted ? "timeout" : "network",
        message: aborted ? "timeout" : String(error),
      },
    };
  } finally {
    clearTimeout(timer);
  }
}

const enc = encodeURIComponent;

export const api = {
  health: () => request<Health>("GET", "/healthz"),
  scenarios: () => request<ScenarioInfo[]>("GET", "/scenarios"),
  matches: () => request<MatchInfo[]>("GET", "/matches"),
  createMatch: (body: Partial<CreateMatch> & { scenario: CreateMatch["scenario"] }) =>
    request<MatchInfo>("POST", "/matches", { body }),
  match: (id: string) => request<MatchView>("GET", `/matches/${enc(id)}`),
  players: (id: string) => request<PlayerBrief[]>("GET", `/matches/${enc(id)}/players`),
  start: (id: string) => request<MatchInfo>("POST", `/matches/${enc(id)}/start`),
  pause: (id: string) => request<MatchInfo>("POST", `/matches/${enc(id)}/pause`),
  resume: (id: string) => request<MatchInfo>("POST", `/matches/${enc(id)}/resume`),
  speed: (id: string, speed: number) =>
    request<MatchInfo>("POST", `/matches/${enc(id)}/speed`, { body: { speed } }),
  verifyClaim: (matchId: string, text: string) =>
    request<ClaimResult>("POST", "/claims/verify", {
      body: { match_id: matchId, text, language: "auto" },
      timeoutMs: CLAIM_TIMEOUT_MS,
    }),
  claim: (claimId: string) => request<ClaimResult>("GET", `/claims/${enc(claimId)}`),
  studioItem: (id: string, itemId: string) =>
    request<StudioItemDetail>("GET", `/matches/${enc(id)}/studio/${enc(itemId)}`),
  talkingPointStatus: (id: string, itemId: string, status: TalkingPoint["status"]) =>
    request<TalkingPoint>("POST", `/matches/${enc(id)}/talking-points/${enc(itemId)}/status`, {
      body: { status },
    }),
  onAir: (id: string) => request<OnAirState>("GET", `/matches/${enc(id)}/on-air`),
  commandOnAir: (id: string, command: OnAirCommand) =>
    request<OnAirState>("POST", `/matches/${enc(id)}/on-air`, { body: command }),
  commentary: (id: string, profileQuery: string) =>
    request<CommentaryView[]>("GET", `/matches/${enc(id)}/commentary?${profileQuery}`),
  lowerThirds: (id: string, lang: "vi" | "en") =>
    request<LowerThirdView[]>("GET", `/matches/${enc(id)}/lower-thirds?lang=${lang}`),
  timeline: (id: string) => request<TimelineView>("GET", `/matches/${enc(id)}/timeline`),
  stats: (id: string, period: StatsPanel["period"]) =>
    request<StatsPanel>("GET", `/matches/${enc(id)}/stats?period=${period}`),
  eventsById: (id: string, ids: string[]) =>
    request<EventsPage>("GET", `/matches/${enc(id)}/events?ids=${enc(ids.join(","))}`),
  lastEvents: (id: string, last: number) =>
    request<EventsPage>("GET", `/matches/${enc(id)}/events?last=${last}`),
  recap: (id: string, scope: RecapView["scope"], lang: "vi" | "en", mode: "analyst" | "casual") =>
    request<RecapView>("GET", `/matches/${enc(id)}/recap?scope=${scope}&lang=${lang}&mode=${mode}`),
};

/** "dominant_loser-42" → {scenario, seed}; null for anything else. */
export function parseMatchId(
  id: string,
): { scenario: CreateMatch["scenario"]; seed: number } | null {
  const found = /^(dominant_loser|comeback_var|red_card_swing|balanced)-(\d{1,7})$/.exec(id);
  if (!found) return null;
  return { scenario: found[1] as CreateMatch["scenario"], seed: Number(found[2]) };
}
