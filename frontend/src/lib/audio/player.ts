// Plays what the scheduler decides (docs/day5-design.md section 10.4): one HTMLAudioElement for
// the whole page (unlocked by a tap, which mobile browsers require), or the browser's voice. It
// reports `ended` / `failed` once per line; a line that does not start within 12 s has failed.
import type { AudioItem, Source } from "./scheduler";
import type { BrowserVoice } from "./browser-voice";

export type ElementLike = {
  src: string;
  volume: number;
  play: () => Promise<void>;
  pause: () => void;
  load: () => void;
  removeAttribute: (name: string) => void;
  onended: (() => void) | null;
  onerror: (() => void) | null;
  onplaying: (() => void) | null;
};

export type PlayerEvents = {
  ended: (id: string) => void;
  failed: (id: string, source: Source) => void;
  blocked: () => void; // autoplay refused: the listener must tap
  started?: (id: string, source: Source) => void;
};

export type Timers = {
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (id: unknown) => void;
};

export const START_TIMEOUT_MS = 12_000;

/** `element.play()` as a promise: old browsers (and jsdom) return nothing, some throw. */
function playing(element: ElementLike): Promise<void> {
  try {
    return Promise.resolve(element.play());
  } catch (error) {
    return Promise.reject(error);
  }
}

/** 50 ms of silence (8 kHz, 16-bit, mono) as a data URI: played in a click to unlock audio. */
export function silentWav(): string {
  const samples = 400;
  const bytes = new Uint8Array(44 + samples * 2);
  const view = new DataView(bytes.buffer);
  const text = (at: number, value: string) =>
    [...value].forEach((ch, i) => view.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 8000, true);
  view.setUint32(28, 16000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples * 2, true);
  let binary = "";
  bytes.forEach((byte) => (binary += String.fromCharCode(byte)));
  return `data:audio/wav;base64,${btoa(binary)}`;
}

export class AudioPlayer {
  private current: { id: string; source: Source } | null = null;
  private startTimer: unknown = null;

  constructor(
    private readonly element: ElementLike | null,
    private readonly voice: BrowserVoice,
    private readonly base: string,
    private readonly events: PlayerEvents,
    private readonly timers: Timers = {
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
    },
  ) {}

  /** In a click handler: play silence on the page's element and wake the browser's voice. */
  unlock(): void {
    const element = this.element;
    if (element) {
      element.src = silentWav();
      playing(element).catch(() => {
        /* the next play() will tell */
      });
    }
    this.voice.unlock();
  }

  /** Warm the HTTP cache with a clip that will be read soon (clips never change). */
  prefetch(url: string): void {
    if (typeof fetch === "undefined") return;
    fetch(this.base + url).catch(() => {
      /* the player fetches it again when its turn comes */
    });
  }

  play(item: AudioItem, source: Source): void {
    this.stop();
    const id = item.id;
    this.current = { id, source };
    if (source === "browser") {
      this.events.started?.(id, source);
      this.voice.speak(item.cue, {
        onend: () => this.finish(id, "ended"),
        onerror: () => this.finish(id, "failed"),
      });
      return;
    }
    const element = this.element;
    const audio = item.cue.audio;
    if (!element || !audio) {
      this.finish(id, "failed");
      return;
    }
    element.onended = () => this.finish(id, "ended");
    element.onerror = () => this.finish(id, "failed");
    element.onplaying = () => {
      this.clearStart();
      this.events.started?.(id, source);
    };
    element.volume = 1;
    element.src = this.base + audio.url;
    this.startTimer = this.timers.setTimer(() => this.finish(id, "failed"), START_TIMEOUT_MS);
    playing(element).catch((error: unknown) => {
      if (this.current?.id !== id) return; // stopped meanwhile
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        this.stop();
        this.events.blocked();
        return;
      }
      this.finish(id, "failed");
    });
  }

  stop(): void {
    this.clearStart();
    this.voice.cancel();
    const element = this.element;
    if (element) {
      element.onended = null;
      element.onerror = null;
      element.onplaying = null;
      element.pause();
      element.removeAttribute("src");
      element.load();
    }
    this.current = null;
  }

  private clearStart(): void {
    if (this.startTimer !== null) this.timers.clearTimer(this.startTimer);
    this.startTimer = null;
  }

  private finish(id: string, kind: "ended" | "failed"): void {
    const current = this.current;
    if (!current || current.id !== id) return; // a late event of a line already replaced
    this.clearStart();
    this.current = null;
    if (kind === "ended") this.events.ended(id);
    else this.events.failed(id, current.source);
  }
}
