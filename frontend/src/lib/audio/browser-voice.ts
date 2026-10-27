// The browser's voice (docs/day5-design.md section 10.4): the fallback when the server has no
// clip. It reads the same segments as the SSML, with the same rate, pitch and volume (factors,
// 1 = the voice's own), so the words and the numbers never differ from the server's voice.
import type { SpeechCue } from "@/lib/contracts";

export type VoiceLike = { name: string; lang: string };
export type UtteranceLike = {
  text: string;
  lang: string;
  voice: VoiceLike | null;
  rate: number;
  pitch: number;
  volume: number;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
};
export type SynthLike = {
  speak: (utterance: UtteranceLike) => void;
  cancel: () => void;
  getVoices: () => VoiceLike[];
  addEventListener?: (type: "voiceschanged", listener: () => void) => void;
};

export const MAX_CHUNK = 200; // Chrome stops long utterances after about 15 s
const NAMES = { female: "HoaiMy", male: "NamMinh" } as const;

/** Split a segment at sentence ends (then at commas, then hard) into chunks of `max` at most. */
export function splitForSpeech(text: string, max = MAX_CHUNK): string[] {
  const chunks: string[] = [];
  let rest = text.trim();
  while (rest.length > max) {
    const window = rest.slice(0, max + 1);
    const cut = Math.max(
      window.lastIndexOf(". "),
      window.lastIndexOf("! "),
      window.lastIndexOf("? "),
      window.lastIndexOf("; "),
    );
    const comma = window.lastIndexOf(", ");
    const space = window.lastIndexOf(" ");
    const at = cut > max / 3 ? cut + 1 : comma > max / 3 ? comma + 1 : space > 0 ? space : max;
    chunks.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) chunks.push(rest);
  return chunks;
}

/** "Microsoft Hoài My Online (Natural)" → "MicrosoftHoaiMyOnline(Natural)": Edge spells the
 * voices with accents and spaces, the Azure names have neither. */
const plainName = (name: string): string =>
  name.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, "");

/** The voice to use: the Azure one by name (Edge has them online), else any voice of the
 * language, else none. */
export function pickVoice(
  voices: readonly VoiceLike[],
  lang: string,
  gender: SpeechCue["gender"],
): VoiceLike | null {
  const prefix = lang.slice(0, 2).toLowerCase();
  const ofLanguage = voices.filter((voice) => voice.lang.toLowerCase().startsWith(prefix));
  return (
    ofLanguage.find((voice) => plainName(voice.name).includes(NAMES[gender])) ??
    ofLanguage[0] ??
    null
  );
}

/** A female and a male voice that differ (Edge: HoaiMy and NamMinh); one system voice (an
 * iPhone has one Vietnamese voice) would read both choices the same. */
export function hasBothGenders(voices: readonly VoiceLike[], lang: string): boolean {
  const female = pickVoice(voices, lang, "female");
  const male = pickVoice(voices, lang, "male");
  return Boolean(female && male && female.name !== male.name);
}

type Callbacks = { onend: () => void; onerror: (reason: string) => void };
type Timers = {
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (id: unknown) => void;
};

export class BrowserVoice {
  private generation = 0;
  private timer: unknown = null;

  constructor(
    private readonly synth: SynthLike | null,
    private readonly makeUtterance: (text: string) => UtteranceLike,
    private readonly timers: Timers = {
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
    },
  ) {}

  /** Voices load late in some browsers: wait for them (at most `ms`). */
  ready(ms = 1000): Promise<void> {
    const synth = this.synth;
    if (!synth || synth.getVoices().length) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => resolve();
      synth.addEventListener?.("voiceschanged", done);
      setTimeout(done, ms);
    });
  }

  available(lang: string, gender: SpeechCue["gender"] = "female"): boolean {
    return Boolean(this.synth && pickVoice(this.synth.getVoices(), lang, gender));
  }

  /** In a click handler: an empty utterance unlocks the voice (iOS). */
  unlock(): void {
    try {
      this.synth?.speak(this.makeUtterance(""));
    } catch {
      /* no voice: nothing to unlock */
    }
  }

  speak(cue: SpeechCue, callbacks: Callbacks): void {
    const synth = this.synth;
    const voice = synth ? pickVoice(synth.getVoices(), cue.lang, cue.gender) : null;
    if (!synth || !voice) {
      callbacks.onerror("no_voice");
      return;
    }
    this.cancel();
    const generation = ++this.generation;
    const parts = cue.segments.flatMap((segment) =>
      splitForSpeech(segment.text).map((text, index) => ({
        text,
        segment,
        pause: index === 0 ? (segment.pause_before_ms ?? 0) : 0,
      })),
    );
    const next = (index: number) => {
      if (generation !== this.generation) return; // cancelled: a newer line or a stop
      const part = parts[index];
      if (!part) {
        callbacks.onend();
        return;
      }
      const say = () => {
        if (generation !== this.generation) return;
        const utterance = this.makeUtterance(part.text);
        utterance.lang = cue.lang;
        utterance.voice = voice;
        utterance.rate = part.segment.rate;
        utterance.pitch = part.segment.pitch;
        utterance.volume = part.segment.volume;
        utterance.onend = () => next(index + 1);
        utterance.onerror = (event) => {
          if (generation !== this.generation) return; // our own cancel: not a failure
          callbacks.onerror(event.error ?? "error");
        };
        synth.speak(utterance);
      };
      if (part.pause > 0) this.timer = this.timers.setTimer(say, part.pause);
      else say();
    };
    next(0);
  }

  cancel(): void {
    this.generation += 1;
    if (this.timer !== null) this.timers.clearTimer(this.timer);
    this.timer = null;
    try {
      this.synth?.cancel();
    } catch {
      /* ignore */
    }
  }
}

/** The page's browser voice, or one that has no voice (SSR, tests, old browsers). */
export function browserVoice(): BrowserVoice {
  const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
  if (!synth || typeof SpeechSynthesisUtterance === "undefined") {
    return new BrowserVoice(null, () => ({}) as UtteranceLike);
  }
  return new BrowserVoice(
    synth as unknown as SynthLike,
    (text) => new SpeechSynthesisUtterance(text) as unknown as UtteranceLike,
  );
}
