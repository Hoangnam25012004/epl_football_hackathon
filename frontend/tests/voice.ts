// Day 5 E2E helpers (docs/day5-design.md section 13, V7): the fan player's audio state
// (`window.punditAudio`), a browser voice for machines that have no Vietnamese one, and the
// speech the server sent on the match sockets.
import type { Page } from "@playwright/test";
import type { CommentaryView } from "@/lib/contracts";

export type AudioView = {
  on: boolean;
  needsGesture: boolean;
  speakingId: string | null;
  source: "server" | "browser" | null;
  waiting: number;
  browserAvailable: boolean;
  browserOnly: boolean;
  paused: boolean;
  recap: boolean;
  waitingIds: string[];
  played: string[];
  maxConcurrent: number;
  playingNow: number;
  counts: {
    played: number;
    dropped: number;
    preempted: number;
    failed: number;
    fallbacks: number;
    skipped: number;
  };
};

export async function audioState(page: Page): Promise<AudioView | null> {
  return page.evaluate(() => (window.punditAudio as unknown as AudioView | undefined) ?? null);
}

/** Replace the browser's voice by one that has HoaiMy and NamMinh and "reads" fast; what it
 * read is in `window.__spoken`. Edge under Playwright has no Vietnamese voice (G0). */
export async function installFakeSpeech(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const voices = [
      { name: "Microsoft HoaiMy Online (Natural) - Vietnamese (Vietnam)", lang: "vi-VN" },
      { name: "Microsoft NamMinh Online (Natural) - Vietnamese (Vietnam)", lang: "vi-VN" },
    ].map((v) => ({ ...v, default: false, localService: false, voiceURI: v.name }));
    type Utterance = {
      text: string;
      voice: { name: string } | null;
      rate: number;
      onend: (() => void) | null;
      onerror: ((event: { error: string }) => void) | null;
    };
    const spoken: { text: string; voice: string | null; rate: number }[] = [];
    let current: Utterance | null = null;
    let timer = 0;
    class FakeUtterance {
      lang = "";
      voice = null;
      rate = 1;
      pitch = 1;
      volume = 1;
      onend = null;
      onerror = null;
      constructor(public text: string) {}
    }
    const synth = {
      speaking: false,
      pending: false,
      paused: false,
      onvoiceschanged: null,
      getVoices: () => voices,
      speak(utterance: Utterance) {
        if (!utterance.text) return; // the unlock in the click
        spoken.push({
          text: utterance.text,
          voice: utterance.voice?.name ?? null,
          rate: utterance.rate,
        });
        current = utterance;
        timer = window.setTimeout(() => {
          current = null;
          utterance.onend?.();
        }, 30 + utterance.text.length);
      },
      cancel() {
        window.clearTimeout(timer);
        const cut = current;
        current = null;
        cut?.onerror?.({ error: "interrupted" });
      },
      pause() {},
      resume() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => true,
    };
    Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      value: FakeUtterance,
      configurable: true,
      writable: true,
    });
    (window as unknown as { __spoken: typeof spoken }).__spoken = spoken;
  });
}

export async function spokenByBrowser(
  page: Page,
): Promise<{ text: string; voice: string | null }[]> {
  return page.evaluate(
    () =>
      (window as unknown as { __spoken?: { text: string; voice: string | null }[] }).__spoken ?? [],
  );
}

export type SpokenLine = {
  id: string;
  n: number;
  excitement: number;
  voice: string;
  url: string | null;
};

/** The commentary with speech that reached the page's match sockets, by id (every connection). */
export function watchSpeech(page: Page): {
  lines: Map<string, SpokenLine>;
  silent: Set<string>;
  sockets: string[];
} {
  const lines = new Map<string, SpokenLine>();
  const silent = new Set<string>(); // commentary without speech
  const sockets: string[] = [];
  page.on("websocket", (ws) => {
    if (!ws.url().includes("/ws/match/")) return;
    sockets.push(ws.url());
    ws.on("framereceived", ({ payload }) => {
      const message = JSON.parse(String(payload)) as {
        type: string;
        n: number;
        payload: CommentaryView;
      };
      if (message.type !== "commentary") return;
      const view = message.payload;
      if (!view.speech) {
        silent.add(view.id);
        return;
      }
      lines.set(view.id, {
        id: view.id,
        n: message.n,
        excitement: view.excitement ?? 0,
        voice: view.speech.voice,
        url: view.speech.audio?.url ?? null,
      });
    });
  });
  return { lines, silent, sockets };
}

/** Console errors without the expected ones (a refused clip logs "Failed to load resource"). */
export function unexpected(errors: string[], allow: RegExp[] = []): string[] {
  return errors.filter((error) => !allow.some((pattern) => pattern.test(error)));
}
