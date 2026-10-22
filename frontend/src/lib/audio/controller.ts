// The fan player's voice, outside React (docs/day5-design.md section 10): live messages in,
// the scheduler decides, the player plays. React reads it through useSyncExternalStore; tests
// and the debug panel read `window.punditAudio` (like `window.punditOverlay`, Day 4).
import type { SpeechCue } from "@/lib/contracts";
import type { AnyWsMessage } from "@/lib/ws-types";
import {
  initialQueue,
  step,
  type AudioItem,
  type Counts,
  type Input,
  type QueueState,
  type Source,
} from "./scheduler";

export type PlayerLike = {
  play: (item: AudioItem, source: Source) => void;
  stop: () => void;
  unlock: () => void;
  prefetch: (url: string) => void;
};

export type AudioSnapshot = {
  on: boolean;
  needsGesture: boolean;
  speakingId: string | null;
  source: Source | null;
  waiting: number;
  browserAvailable: boolean;
  browserOnly: boolean; // the client's breaker sends lines to the browser for now
  counts: Counts;
};

type DebugView = AudioSnapshot & {
  paused: boolean; // the match is paused (R9)
  recap: boolean; // a recap holds the live queue (R10)
  waitingIds: string[];
  played: string[];
  maxConcurrent: number;
  playingNow: number;
};

declare global {
  interface Window {
    punditAudio?: DebugView;
  }
}

export const TICK_MS = 1000;

export class AudioController {
  private state: QueueState;
  private snapshot: AudioSnapshot;
  private listeners = new Set<() => void>();
  private needsGesture = false;
  private ticker: ReturnType<typeof setInterval> | null = null;
  private played: string[] = [];
  private playing = 0;
  private maxConcurrent = 0;
  private greetings = 0;
  player: PlayerLike | null = null;

  constructor(
    private readonly now: () => number = () => performance.now(),
    browserAvailable = false,
  ) {
    this.state = { ...initialQueue(browserAvailable), muted: true };
    this.snapshot = this.makeSnapshot();
  }

  // ------------------------------------------------------------------ React (external store)

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): AudioSnapshot => this.snapshot;

  // ------------------------------------------------------------------ inputs

  dispatch(input: Input): void {
    const finished = input.type === "ended" || input.type === "failed";
    if (finished && this.state.current?.item.id === input.id) this.playing = 0;
    const { state, effects } = step(this.state, input, this.now());
    this.state = state;
    for (const effect of effects) {
      if (effect.type === "play") {
        // a play while a line is still playing (no stop, no end) would be two voices at once:
        // `maxConcurrent` shows it to the tests (it must stay 1)
        this.playing += 1;
        this.maxConcurrent = Math.max(this.maxConcurrent, this.playing);
        this.player?.play(effect.item, effect.source);
      } else {
        this.playing = 0;
        this.player?.stop();
      }
    }
    this.publish();
  }

  /** Every message the socket accepted, in order (useMatchConnection's onLive). */
  onLive = (message: AnyWsMessage): void => {
    switch (message.type) {
      case "hello":
      case "resync":
        this.dispatch({ type: "reset" });
        return;
      case "status": {
        const { session, restarted } = message.payload;
        if (restarted) this.dispatch({ type: "clear" });
        else if (session === "paused") this.dispatch({ type: "pause" });
        else if (session === "running") this.dispatch({ type: "resume" });
        return;
      }
      case "commentary": {
        const cue = message.payload.speech;
        if (!cue || this.state.muted) return;
        if (cue.audio) this.player?.prefetch(cue.audio.url);
        this.dispatch({
          type: "enqueue",
          item: {
            id: message.payload.id,
            n: message.n,
            excitement: message.payload.excitement ?? 0,
            clockMs: message.payload.clock_ms,
            cue,
            arrivedAt: this.now(),
          },
        });
        return;
      }
      default:
        return;
    }
  };

  /** The listener's tap: unlock audio (must run inside the click), then listen. */
  enable(): void {
    this.player?.unlock();
    this.needsGesture = false;
    this.dispatch({ type: "unmute" });
    this.startTicking();
  }

  disable(): void {
    this.dispatch({ type: "mute" });
    this.stopTicking();
  }

  /** The greeting (AI voice, synthetic data), read like any other line. */
  greet(cue: SpeechCue): void {
    this.greetings += 1;
    this.dispatch({
      type: "enqueue",
      item: {
        id: `greeting-${this.greetings}`,
        n: 0,
        excitement: 2,
        clockMs: 0,
        cue,
        arrivedAt: this.now(),
        greeting: true,
      },
    });
  }

  setBrowserAvailable(available: boolean): void {
    this.dispatch({ type: "browser", available });
  }

  // ------------------------------------------------------------------ player events

  ended = (id: string): void => {
    queueMicrotask(() => {
      if (this.state.current?.item.id === id) this.played = [...this.played, id].slice(-50);
      this.dispatch({ type: "ended", id });
    });
  };

  failed = (id: string, source: Source): void => {
    queueMicrotask(() => this.dispatch({ type: "failed", id, source }));
  };

  blocked = (): void => {
    queueMicrotask(() => {
      this.needsGesture = true;
      this.dispatch({ type: "mute" });
    });
  };

  // ------------------------------------------------------------------ lifecycle

  private startTicking(): void {
    if (this.ticker !== null) return;
    this.ticker = setInterval(() => this.dispatch({ type: "tick" }), TICK_MS);
  }

  private stopTicking(): void {
    if (this.ticker !== null) clearInterval(this.ticker);
    this.ticker = null;
  }

  /** The page is leaving: silence, no ticker. Listeners stay (React unsubscribes itself). */
  release(): void {
    this.disable();
    this.player?.stop();
  }

  private makeSnapshot(): AudioSnapshot {
    const s = this.state;
    return {
      on: !s.muted,
      needsGesture: this.needsGesture,
      speakingId: s.current?.item.id ?? null,
      source: s.current?.source ?? null,
      waiting: s.waiting.length,
      browserAvailable: s.browserAvailable,
      browserOnly: this.now() < s.browserUntil,
      counts: s.counts,
    };
  }

  private publish(): void {
    this.snapshot = this.makeSnapshot();
    if (typeof window !== "undefined") {
      window.punditAudio = {
        ...this.snapshot,
        paused: this.state.paused,
        recap: this.state.recap,
        waitingIds: this.state.waiting.map((item) => item.id),
        played: this.played,
        maxConcurrent: this.maxConcurrent,
        playingNow: this.playing,
      };
    }
    for (const listener of this.listeners) listener();
  }
}
