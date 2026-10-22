// The viewer profile on the client (docs/day4-design.md section 4.7): the WebSocket query that
// `parse_profile` reads, kept in localStorage when the browser allows it.
export type Mode = "analyst" | "casual";
export type Lang = "vi" | "en";
export type VoiceGender = "female" | "male";

export type ViewerProfile = {
  mode: Mode;
  language: Lang;
  team: string | null; // favourite club (team id)
  player: string | null; // player focus (player id)
  beginner: boolean; // "Người mới xem"
  metric: string | null; // one metric only
  voice: VoiceGender | null; // Day 5: the voice chosen (it is heard only once turned on)
};

export const DEFAULT_PROFILE: ViewerProfile = {
  mode: "casual",
  language: "vi",
  team: null,
  player: null,
  beginner: false,
  metric: null,
  voice: null,
};

const STORAGE_KEY = "pundit.profile";
const VOICE_ON_KEY = "pundit.voice-on";
const ID = /^[a-z0-9_-]{1,64}$/;

/** The WebSocket query; `voice` only while the voice is on (the server then reads aloud). */
export function profileQuery(profile: ViewerProfile): string {
  const params = new URLSearchParams();
  params.set("mode", profile.mode);
  params.set("lang", profile.language);
  if (profile.team) params.set("club", profile.team);
  if (profile.player) params.set("player", profile.player);
  if (profile.beginner) params.set("beginner", "1");
  if (profile.metric) params.set("metric", profile.metric);
  if (profile.voice) params.set("voice", profile.voice);
  return params.toString();
}

/** Was the voice on when the page was left? (A tap is still needed to hear it again.) */
export function loadVoiceOn(storage: Pick<Storage, "getItem"> | null = safeStorage()): boolean {
  try {
    return storage?.getItem(VOICE_ON_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveVoiceOn(on: boolean, storage: Pick<Storage, "setItem"> | null = safeStorage()) {
  try {
    storage?.setItem(VOICE_ON_KEY, on ? "1" : "0");
  } catch {
    /* storage blocked: the choice lives for this page only */
  }
}

/** Anything that is not a well-formed profile becomes the default, field by field. */
export function coerceProfile(value: unknown): ViewerProfile {
  const source = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
  const pick = (key: string): string | null =>
    typeof source[key] === "string" && ID.test(source[key] as string)
      ? (source[key] as string)
      : null;
  return {
    mode: source.mode === "analyst" ? "analyst" : "casual",
    language: source.language === "en" ? "en" : "vi",
    team: pick("team"),
    player: pick("player"),
    beginner: source.beginner === true,
    metric: pick("metric"),
    voice: source.voice === "female" || source.voice === "male" ? source.voice : null,
  };
}

/** Drop a club or a player that does not belong to this match (a 4422 loop otherwise). */
export function fitProfile(
  profile: ViewerProfile,
  teams: readonly string[],
  players: readonly string[] | null,
): ViewerProfile {
  return {
    ...profile,
    team: profile.team && teams.includes(profile.team) ? profile.team : null,
    player:
      profile.player && (players === null || players.includes(profile.player))
        ? profile.player
        : null,
  };
}

export function loadProfile(
  storage: Pick<Storage, "getItem"> | null = safeStorage(),
): ViewerProfile | null {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    return raw ? coerceProfile(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

export function saveProfile(
  profile: ViewerProfile,
  storage: Pick<Storage, "setItem"> | null = safeStorage(),
): void {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch {
    /* private mode or storage blocked: the profile lives for this page only */
  }
}

export function clearProfile(storage: Pick<Storage, "removeItem"> | null = safeStorage()): void {
  try {
    storage?.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
