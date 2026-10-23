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
  RecognitionResult,
  ScenarioInfo,
  SpeechCue,
  SpeechVoice,
  StatsPanel,
  StudioItemDetail,
  TalkingPoint,
  TimelineView,
} from "./contracts";

export type ApiErrorCode =
  "not_found" | "conflict" | "invalid" | "server" | "network" | "timeout" | "busy" | "other";

// `busy` (Day 6): the server asks to come back later (429 too_many_sessions, 503 demo_full)
export type ApiError = {
  status: number;
  code: ApiErrorCode;
  message: string;
  retryAfterS?: number;
};
export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

export const DEFAULT_TIMEOUT_MS = 8_000;
export const CLAIM_TIMEOUT_MS = 20_000; // a claim may wait for its LLM budget
export const STT_TIMEOUT_MS = 20_000; // a recording may wait its turn (one at a time)

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

/** `retry_after_s` of a refusal that says when to come back (Day 6). */
function retryAfterOf(body: unknown): number | undefined {
  if (body === null || typeof body !== "object" || !("retry_after_s" in body)) return undefined;
  const value = Number((body as { retry_after_s: unknown }).retry_after_s);
  return Number.isFinite(value) && value > 0 ? value : undefined;
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

type RequestOptions = {
  body?: unknown; // sent as JSON
  blob?: Blob; // sent as is, with its type (Day 5: the WAV of a spoken claim)
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
};

export async function request<T>(
  method: "GET" | "POST",
  path: string,
  { body, blob, timeoutMs = DEFAULT_TIMEOUT_MS, fetchImpl = fetch }: RequestOptions = {},
): Promise<Result<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers = blob
    ? { "Content-Type": blob.type }
    : body === undefined
      ? undefined
      : { "Content-Type": "application/json" };
  try {
    const response = await fetchImpl(apiBase() + path, {
      method,
      headers,
      body: blob ?? (body === undefined ? undefined : JSON.stringify(body)),
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
      const retryAfterS = retryAfterOf(json);
      return {
        ok: false,
        error: {
          status: response.status,
          code: retryAfterS === undefined ? codeOf(response.status) : "busy",
          message,
          ...(retryAfterS === undefined ? {} : { retryAfterS }),
        },
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
  recap: (
    id: string,
    scope: RecapView["scope"],
    lang: "vi" | "en",
    mode: "analyst" | "casual",
    voice: "female" | "male" | null = null,
  ) =>
    request<RecapView>(
      "GET",
      `/matches/${enc(id)}/recap?scope=${scope}&lang=${lang}&mode=${mode}` +
        (voice ? `&voice=${voice}` : ""),
    ),
  // Day 5: the voice (docs/day5-design.md section 3.5)
  voices: () => request<SpeechVoice[]>("GET", "/speech/voices"),
  greeting: (lang: "vi" | "en", voice: "female" | "male") =>
    request<SpeechCue>("GET", `/speech/greeting?lang=${lang}&voice=${voice}`),
  // P2: a spoken claim as text, for the claim box only (docs/day5-design.md section 11)
  recognize: (wav: Blob, lang: "vi" | "en") =>
    request<RecognitionResult>("POST", `/speech/recognize?lang=${lang}`, {
      blob: wav,
      timeoutMs: STT_TIMEOUT_MS,
    }),
};

export type ParsedMatchId = {
  scenario: CreateMatch["scenario"];
  seed: number;
  token: string | null; // a private session's token (Day 6), null for the shared session
};

/** "dominant_loser-42" or "dominant_loser-42~k3v9x2ma" → its parts; null for anything else. */
export function parseMatchId(id: string): ParsedMatchId | null {
  const found =
    /^(dominant_loser|comeback_var|red_card_swing|balanced)-(\d{1,7})(?:~([a-z0-9]{8}))?$/.exec(id);
  if (!found) return null;
  return {
    scenario: found[1] as CreateMatch["scenario"],
    seed: Number(found[2]),
    token: found[3] ?? null,
  };
}

/** The body that makes (again) the session an id names: a private one keeps its token. */
export function sessionBody(parsed: ParsedMatchId): Pick<CreateMatch, "scenario" | "seed"> & {
  session?: string;
} {
  return {
    scenario: parsed.scenario,
    seed: parsed.seed,
    ...(parsed.token ? { session: parsed.token } : {}),
  };
}

/** Is `id` a private session of one viewer? */
export function isPrivate(id: string): boolean {
  return parseMatchId(id)?.token != null;
}
