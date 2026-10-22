// The fan player's voice outside React (docs/day5-design.md section 10.4): the page's audio
// element, the browser's voice and the controller between the socket and the scheduler, each
// with a fake (element, speechSynthesis, player) and fake timers.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BrowserVoice,
  MAX_CHUNK,
  pickVoice,
  splitForSpeech,
  type SynthLike,
  type UtteranceLike,
  type VoiceLike,
} from "@/lib/audio/browser-voice";
import { AudioController, type PlayerLike } from "@/lib/audio/controller";
import { AudioPlayer, START_TIMEOUT_MS, silentWav, type ElementLike } from "@/lib/audio/player";
import type { AudioItem, Source } from "@/lib/audio/scheduler";
import type { SpeechCue } from "@/lib/contracts";
import type { AnyWsMessage } from "@/lib/ws-types";
import { fixture, ofType } from "../fixtures";

function cue(withAudio = true, segments: SpeechCue["segments"] = []): SpeechCue {
  return {
    lang: "vi-VN",
    voice: "vi-VN-HoaiMyNeural",
    gender: "female",
    preset: "calm",
    segments: segments.length
      ? segments
      : [
          {
            text: "Phút hai mươi bảy, Ribas ghi bàn.",
            preset: "peak",
            rate: 1.2,
            pitch: 1.1,
            volume: 1,
          },
        ],
    audio: withAudio
      ? {
          clip_id: "a".repeat(32),
          url: `/audio/${"a".repeat(32)}.wav`,
          media_type: "audio/wav",
          duration_ms: 1500,
          exact: true,
        }
      : null,
    note: withAudio ? null : "circuit_open",
  };
}

const item = (id: string, withAudio = true): AudioItem => ({
  id,
  n: 1,
  excitement: 1,
  clockMs: 0,
  cue: cue(withAudio),
  arrivedAt: 0,
});

class Timers {
  private next = 1;
  pending = new Map<number, { fn: () => void; ms: number }>();
  setTimer = (fn: () => void, ms: number) => {
    const id = this.next++;
    this.pending.set(id, { fn, ms });
    return id;
  };
  clearTimer = (id: unknown) => {
    this.pending.delete(id as number);
  };
  /** Run every timer due (in creation order). */
  fire(): void {
    for (const [id, timer] of [...this.pending]) {
      this.pending.delete(id);
      timer.fn();
    }
  }
}

class FakeElement implements ElementLike {
  src = "";
  volume = 0.5;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onplaying: (() => void) | null = null;
  played: string[] = [];
  pauses = 0;
  outcome: "ok" | "blocked" | "broken" = "ok";
  play = () => {
    this.played.push(this.src);
    if (this.outcome === "blocked") {
      return Promise.reject(new DOMException("autoplay", "NotAllowedError"));
    }
    if (this.outcome === "broken") return Promise.reject(new Error("decode"));
    return Promise.resolve();
  };
  pause = () => {
    this.pauses += 1;
  };
  load = () => {};
  removeAttribute = (name: string) => {
    if (name === "src") this.src = "";
  };
}

class FakeSynth implements SynthLike {
  voices: VoiceLike[] = [];
  spoken: UtteranceLike[] = [];
  cancels = 0;
  listeners: (() => void)[] = [];
  speak = (utterance: UtteranceLike) => {
    this.spoken.push(utterance);
  };
  cancel = () => {
    this.cancels += 1;
    // Chrome reports the utterance it cut as an error ("interrupted")
    this.spoken.at(-1)?.onerror?.({ error: "interrupted" });
  };
  getVoices = () => this.voices;
  addEventListener = (_type: "voiceschanged", listener: () => void) => {
    this.listeners.push(listener);
  };
}

const utterance = (text: string): UtteranceLike => ({
  text,
  lang: "",
  voice: null,
  rate: 1,
  pitch: 1,
  volume: 1,
  onend: null,
  onerror: null,
});

const VI_FEMALE = {
  name: "Microsoft HoaiMy Online (Natural) - Vietnamese (Vietnam)",
  lang: "vi-VN",
};
const VI_MALE = {
  name: "Microsoft NamMinh Online (Natural) - Vietnamese (Vietnam)",
  lang: "vi-VN",
};
const EN = { name: "Microsoft David - English (United States)", lang: "en-US" };

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup(outcome: FakeElement["outcome"] = "ok", voices: VoiceLike[] = [VI_FEMALE]) {
  const element = new FakeElement();
  element.outcome = outcome;
  const synth = new FakeSynth();
  synth.voices = voices;
  const timers = new Timers();
  const voice = new BrowserVoice(synth, utterance, timers);
  const events = {
    ended: vi.fn<(id: string) => void>(),
    failed: vi.fn<(id: string, source: Source) => void>(),
    blocked: vi.fn<() => void>(),
    started: vi.fn<(id: string, source: Source) => void>(),
  };
  const player = new AudioPlayer(element, voice, "http://api", events, timers);
  return { element, synth, timers, voice, events, player };
}

describe("AudioPlayer", () => {
  it("plays a clip of the server and reports its end once", async () => {
    const { element, events, player } = setup();
    player.play(item("c1"), "server");
    expect(element.src).toBe(`http://api/audio/${"a".repeat(32)}.wav`);
    expect(element.volume).toBe(1);
    element.onplaying?.();
    expect(events.started).toHaveBeenCalledWith("c1", "server");
    element.onended?.();
    element.onended?.(); // a second event of the same line is ignored
    await flush();
    expect(events.ended).toHaveBeenCalledTimes(1);
    expect(events.failed).not.toHaveBeenCalled();
  });

  it("autoplay refused: blocked (a tap is needed), not failed", async () => {
    const { events, player } = setup("blocked");
    player.play(item("c1"), "server");
    await flush();
    expect(events.blocked).toHaveBeenCalledTimes(1);
    expect(events.failed).not.toHaveBeenCalled();
  });

  it("a broken clip, an error event or 12 s without sound: failed", async () => {
    const broken = setup("broken");
    broken.player.play(item("c1"), "server");
    await flush();
    expect(broken.events.failed).toHaveBeenCalledWith("c1", "server");

    const errored = setup();
    errored.player.play(item("c2"), "server");
    errored.element.onerror?.();
    expect(errored.events.failed).toHaveBeenCalledWith("c2", "server");

    const silent = setup();
    silent.player.play(item("c3"), "server");
    expect([...silent.timers.pending.values()].map((t) => t.ms)).toEqual([START_TIMEOUT_MS]);
    silent.timers.fire();
    expect(silent.events.failed).toHaveBeenCalledWith("c3", "server");

    const started = setup();
    started.player.play(item("c4"), "server");
    started.element.onplaying?.(); // sound: the start timer is cleared
    expect(started.timers.pending.size).toBe(0);
  });

  it("stop silences the line and its late events are ignored", async () => {
    const { element, events, player } = setup();
    player.play(item("c1"), "server");
    const lateEnd = element.onended;
    const pauses = element.pauses;
    player.stop();
    expect(element.src).toBe("");
    expect(element.pauses).toBe(pauses + 1);
    lateEnd?.();
    await flush();
    expect(events.ended).not.toHaveBeenCalled();
    expect(events.failed).not.toHaveBeenCalled();
  });

  it("a line without a clip on the server path fails at once", () => {
    const { events, player } = setup();
    player.play(item("c1", false), "server");
    expect(events.failed).toHaveBeenCalledWith("c1", "server");
  });

  it("reads with the browser's voice and reports its end", () => {
    const { synth, events, player } = setup();
    player.play(item("c1", false), "browser");
    expect(synth.spoken).toHaveLength(1);
    synth.spoken[0]?.onend?.();
    expect(events.ended).toHaveBeenCalledWith("c1");
  });

  it("unlock plays silence on the element and wakes the browser's voice", () => {
    const { element, synth, player } = setup();
    player.unlock();
    expect(element.played).toEqual([silentWav()]);
    expect(synth.spoken.map((u) => u.text)).toEqual([""]);
    expect(silentWav()).toMatch(/^data:audio\/wav;base64,UklGR/); // "RIFF"
  });

  it("prefetch warms the cache and never throws", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    const { player } = setup();
    player.prefetch("/audio/x.wav");
    await flush();
    expect(fetchSpy).toHaveBeenCalledWith("http://api/audio/x.wav");
    fetchSpy.mockRestore();
  });
});

describe("BrowserVoice", () => {
  it("picks the Azure voice by name, else any voice of the language, else none", () => {
    expect(pickVoice([EN, VI_MALE, VI_FEMALE], "vi-VN", "female")).toBe(VI_FEMALE);
    expect(pickVoice([EN, VI_FEMALE, VI_MALE], "vi-VN", "male")).toBe(VI_MALE);
    const other = { name: "Google tiếng Việt", lang: "vi_VN" };
    expect(pickVoice([EN, other], "vi-VN", "male")).toBe(other);
    expect(pickVoice([EN], "vi-VN", "female")).toBeNull();
  });

  it("splits long text at sentence ends, then commas, then spaces", () => {
    const sentence = "Harbor Lions dâng cao đội hình và ép sân liên tục trong hiệp hai. ";
    const chunks = splitForSpeech(sentence.repeat(8));
    expect(chunks.length).toBeGreaterThan(1);
    for (const chunk of chunks) {
      expect(chunk.length).toBeLessThanOrEqual(MAX_CHUNK);
      expect(chunk.endsWith(".")).toBe(true);
    }
    expect(chunks.join(" ")).toBe(sentence.repeat(8).trim());
    const words = splitForSpeech("một ".repeat(120));
    expect(words.every((chunk) => chunk.length <= MAX_CHUNK && !chunk.startsWith(" "))).toBe(true);
    expect(splitForSpeech("x".repeat(450)).map((c) => c.length)).toEqual([200, 200, 50]);
    expect(splitForSpeech("  ")).toEqual([]);
  });

  it("reads the segments in order with their prosody and pauses", () => {
    const { synth, timers, voice } = setup();
    const onend = vi.fn();
    const onerror = vi.fn();
    voice.speak(
      cue(false, [
        { text: "Vào!", preset: "burst", rate: 1.3, pitch: 1.2, volume: 1 },
        {
          text: "Ribas ghi bàn.",
          preset: "peak",
          rate: 1.15,
          pitch: 1.1,
          volume: 1,
          pause_before_ms: 250,
        },
      ]),
      { onend, onerror },
    );
    expect(synth.spoken.map((u) => [u.text, u.rate, u.pitch, u.lang, u.voice?.name])).toEqual([
      ["Vào!", 1.3, 1.2, "vi-VN", VI_FEMALE.name],
    ]);
    synth.spoken[0]?.onend?.();
    expect(synth.spoken).toHaveLength(1); // waits for the pause
    expect([...timers.pending.values()].map((t) => t.ms)).toEqual([250]);
    timers.fire();
    expect(synth.spoken.map((u) => u.text)).toEqual(["Vào!", "Ribas ghi bàn."]);
    expect(onend).not.toHaveBeenCalled();
    synth.spoken[1]?.onend?.();
    expect(onend).toHaveBeenCalledTimes(1);
    expect(onerror).not.toHaveBeenCalled();
  });

  it("cancel stops it silently (its own interruption is not an error)", () => {
    const { synth, timers, voice } = setup();
    const onend = vi.fn();
    const onerror = vi.fn();
    voice.speak(
      cue(false, [
        { text: "Một.", preset: "calm", rate: 1, pitch: 1, volume: 1 },
        { text: "Hai.", preset: "calm", rate: 1, pitch: 1, volume: 1, pause_before_ms: 300 },
      ]),
      { onend, onerror },
    );
    synth.spoken[0]?.onend?.();
    voice.cancel();
    timers.fire(); // the pause timer was cleared: nothing more is said
    expect(synth.spoken).toHaveLength(1);
    expect(onend).not.toHaveBeenCalled();
    expect(onerror).not.toHaveBeenCalled();
  });

  it("a real error of the voice is reported; no voice for the language fails at once", () => {
    const { synth, voice } = setup();
    const onerror = vi.fn();
    voice.speak(cue(false), { onend: vi.fn(), onerror });
    synth.spoken[0]?.onerror?.({ error: "synthesis-failed" });
    expect(onerror).toHaveBeenCalledWith("synthesis-failed");

    const none = setup("ok", [EN]);
    const failed = vi.fn();
    none.voice.speak(cue(false), { onend: vi.fn(), onerror: failed });
    expect(failed).toHaveBeenCalledWith("no_voice");
    expect(none.voice.available("vi-VN")).toBe(false);
    expect(new BrowserVoice(null, utterance).available("vi-VN")).toBe(false);
  });

  it("waits for voices that load late (voiceschanged), at most the time given", async () => {
    const synth = new FakeSynth();
    const voice = new BrowserVoice(synth, utterance);
    const ready = voice.ready(5000);
    synth.voices = [VI_MALE];
    synth.listeners.forEach((listener) => listener());
    await ready;
    expect(voice.available("vi-VN", "male")).toBe(true);
    vi.useFakeTimers();
    try {
      const quiet = new BrowserVoice(new FakeSynth(), utterance).ready(1000);
      vi.advanceTimersByTime(1000);
      await expect(quiet).resolves.toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });
});

// ------------------------------------------------------------------------------ controller

class FakePlayer implements PlayerLike {
  plays: [string, Source][] = [];
  stops = 0;
  unlocks = 0;
  prefetched: string[] = [];
  play = (playing: AudioItem, source: Source) => {
    this.plays.push([playing.id, source]);
  };
  stop = () => {
    this.stops += 1;
  };
  unlock = () => {
    this.unlocks += 1;
  };
  prefetch = (url: string) => {
    this.prefetched.push(url);
  };
}

const live = ofType(fixture("dominant_loser-42.casual-vi-beginner-har"), "commentary");

function spoken(index: number, withAudio = true): AnyWsMessage {
  const message = live[index];
  if (!message) throw new Error("fixture too short");
  return { ...message, payload: { ...message.payload, speech: cue(withAudio) } };
}

const status = (session: "paused" | "running", restarted = false): AnyWsMessage => ({
  v: 1,
  type: "status",
  n: 9999,
  match_id: "dominant_loser-42",
  payload: { session, restarted, speed: 20, degraded: [], error: null },
});

function controllerSetup() {
  let now = 0;
  const controller = new AudioController(() => now, true);
  const player = new FakePlayer();
  controller.player = player;
  return { controller, player, tick: (ms: number) => (now += ms) };
}

describe("AudioController", () => {
  afterEach(() => {
    delete window.punditAudio;
  });

  it("is silent until enabled; then reads every live line with speech, in order", async () => {
    const { controller, player } = controllerSetup();
    controller.onLive(spoken(0));
    expect(player.plays).toEqual([]); // off: nothing is queued
    controller.enable();
    expect(player.unlocks).toBe(1);
    controller.onLive(spoken(1));
    controller.onLive(spoken(2));
    const first = live[1]?.payload.id ?? "";
    const second = live[2]?.payload.id ?? "";
    expect(player.plays).toEqual([[first, "server"]]);
    expect(player.prefetched).toHaveLength(2);
    controller.ended(first);
    await flush();
    expect(player.plays).toEqual([
      [first, "server"],
      [second, "server"],
    ]);
    expect(window.punditAudio).toMatchObject({ played: [first], maxConcurrent: 1, on: true });
    controller.release();
  });

  it("ignores lines without speech and the messages it does not need", () => {
    const { controller, player } = controllerSetup();
    controller.enable();
    const plain = live[0];
    if (!plain) throw new Error("fixture too short");
    controller.onLive(plain);
    expect(player.plays).toEqual([]);
    controller.release();
  });

  it("a hello or resync drops what waited, the line being read goes on", () => {
    const { controller, player } = controllerSetup();
    controller.enable();
    controller.onLive(spoken(1));
    controller.onLive(spoken(2));
    const hello = fixture("dominant_loser-42.casual-vi-beginner-har").find(
      (m) => m.type === "hello",
    );
    if (!hello) throw new Error("no hello");
    controller.onLive(hello);
    expect(controller.getSnapshot()).toMatchObject({ waiting: 0, speakingId: live[1]?.payload.id });
    expect(player.stops).toBe(0);
    controller.release();
  });

  it("pause, resume and a restart from the studio", async () => {
    const { controller, player } = controllerSetup();
    controller.enable();
    controller.onLive(spoken(1));
    controller.onLive(status("paused"));
    controller.onLive(spoken(2));
    controller.ended(live[1]?.payload.id ?? "");
    await flush();
    expect(player.plays).toHaveLength(1); // paused: no new line
    controller.onLive(status("running"));
    expect(player.plays).toHaveLength(2);
    controller.onLive(status("running", true));
    expect(player.stops).toBe(1);
    expect(controller.getSnapshot()).toMatchObject({ speakingId: null, waiting: 0, on: true });
    controller.release();
  });

  it("autoplay refused: needs a tap, and the voice is off until then", async () => {
    const { controller } = controllerSetup();
    controller.enable();
    controller.blocked();
    await flush();
    expect(controller.getSnapshot()).toMatchObject({ needsGesture: true, on: false });
    controller.enable();
    expect(controller.getSnapshot()).toMatchObject({ needsGesture: false, on: true });
    controller.release();
  });

  it("a failed clip is read by the browser; three failures: browser only", async () => {
    const { controller, player } = controllerSetup();
    controller.enable();
    for (const index of [1, 2, 3]) {
      controller.onLive(spoken(index));
      const id = live[index]?.payload.id ?? "";
      controller.failed(id, "server");
      await flush();
      controller.ended(id);
      await flush();
    }
    expect(player.plays.map(([, source]) => source)).toEqual([
      "server",
      "browser",
      "server",
      "browser",
      "server",
      "browser",
    ]);
    expect(controller.getSnapshot()).toMatchObject({ browserOnly: true });
    expect(controller.getSnapshot().counts.fallbacks).toBe(3);
    controller.onLive(spoken(4));
    expect(player.plays.at(-1)).toEqual([live[4]?.payload.id, "browser"]);
    controller.release();
  });

  it("greets first, keeps the greeting across a new hello, and skips", async () => {
    const { controller, player } = controllerSetup();
    controller.enable();
    controller.onLive(spoken(1));
    controller.greet(cue());
    const hello = fixture("dominant_loser-42.casual-vi-beginner-har").find(
      (m) => m.type === "hello",
    );
    if (!hello) throw new Error("no hello");
    controller.onLive(hello);
    expect(controller.getSnapshot().waiting).toBe(1); // the greeting
    controller.dispatch({ type: "skip" });
    expect(player.plays.at(-1)).toEqual(["greeting-1", "server"]);
    controller.disable();
    expect(controller.getSnapshot()).toMatchObject({ on: false, speakingId: null });
    controller.release();
  });

  it("notifies React only through its snapshot (a new object on every change)", () => {
    const { controller } = controllerSetup();
    const listener = vi.fn();
    const unsubscribe = controller.subscribe(listener);
    const before = controller.getSnapshot();
    controller.enable();
    expect(listener).toHaveBeenCalled();
    expect(controller.getSnapshot()).not.toBe(before);
    expect(controller.getSnapshot()).toBe(controller.getSnapshot()); // stable between changes
    unsubscribe();
    controller.release();
  });
});
