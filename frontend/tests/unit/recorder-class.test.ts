// The Recorder with fake Web Audio objects (docs/day5-design.md section 11): `start` resolves only
// once the microphone delivers sound (first use took 1.4 s in Edge), the WAV holds what was
// captured (resampled from 48 kHz when the browser ignored 16 kHz), and everything is released.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCommentaryAudio } from "@/hooks/useCommentaryAudio";
import { LIVE_TIMEOUT_MS, MAX_SECONDS, Recorder, RecorderError } from "@/lib/audio/recorder";

type Port = { onmessage: ((event: MessageEvent<Float32Array>) => void) | null };

const audio = vi.hoisted(() => ({
  rate: 16_000,
  refuseRate: false,
  state: "running" as AudioContextState,
  ports: [] as Port[],
  closed: 0,
  disconnected: 0,
  modules: [] as string[],
  failModule: false,
}));

class FakeNode {
  connect() {}
  disconnect() {
    audio.disconnected += 1;
  }
}

class FakeContext {
  sampleRate: number;
  state: AudioContextState;
  destination = new FakeNode();
  audioWorklet = {
    addModule: async (url: string) => {
      audio.modules.push(url);
      if (audio.failModule) throw new Error("module failed");
    },
  };
  constructor(options?: { sampleRate?: number }) {
    if (options?.sampleRate && audio.refuseRate)
      throw new DOMException("rate", "NotSupportedError");
    this.sampleRate = options?.sampleRate ?? audio.rate;
    this.state = audio.state;
  }
  createMediaStreamSource() {
    return new FakeNode();
  }
  createGain() {
    return Object.assign(new FakeNode(), { gain: { value: 1 } });
  }
  async resume() {
    this.state = "running";
  }
  async close() {
    audio.closed += 1;
    this.state = "closed";
  }
}

class FakeWorkletNode extends FakeNode {
  port: Port = { onmessage: null };
  constructor() {
    super();
    audio.ports.push(this.port);
  }
}

const track = { stop: vi.fn() };
const getUserMedia = vi.fn(async () => ({ getTracks: () => [track] }));

/** The worklet posts a block of samples to the page. */
function post(samples: Float32Array, port = audio.ports.at(-1)) {
  port?.onmessage?.({ data: samples } as MessageEvent<Float32Array>);
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  Object.assign(audio, {
    rate: 16_000,
    refuseRate: false,
    state: "running",
    ports: [],
    closed: 0,
    disconnected: 0,
    modules: [],
    failModule: false,
  });
  track.stop.mockClear();
  getUserMedia.mockClear();
  vi.stubGlobal("AudioContext", FakeContext);
  vi.stubGlobal("AudioWorkletNode", FakeWorkletNode);
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia },
    configurable: true,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  Reflect.deleteProperty(navigator, "mediaDevices");
});

describe("Recorder", () => {
  it("starts only when sound arrives, then records it as a 16 kHz WAV", async () => {
    const recorder = new Recorder();
    let started = false;
    const start = recorder.start().then(() => (started = true));
    await flush();
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    expect(audio.modules).toEqual(["/worklets/pcm-capture.js"]);
    expect(started).toBe(false); // the microphone has not delivered anything yet
    post(new Float32Array(1600).fill(0.5));
    await start;
    expect(started).toBe(true);
    post(new Float32Array(1600).fill(-0.5));
    expect(recorder.elapsedMs).toBe(200);
    const { wav, durationMs } = recorder.stop();
    expect(durationMs).toBe(200);
    expect(wav.length).toBe(44 + 3200 * 2);
    const view = new DataView(wav.buffer);
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getInt16(44, true)).toBe(16383);
    expect(view.getInt16(44 + 3199 * 2, true)).toBe(-16384);
    expect(track.stop).toHaveBeenCalled();
    expect(audio.closed).toBe(1);
    expect(audio.disconnected).toBe(3);
  });

  it("records at the browser's rate when 16 kHz is refused, and resamples", async () => {
    audio.refuseRate = true;
    audio.rate = 48_000;
    const recorder = new Recorder();
    const start = recorder.start();
    await flush();
    post(new Float32Array(4800).fill(0.25)); // 100 ms at 48 kHz
    await start;
    const { wav, durationMs } = recorder.stop();
    expect(durationMs).toBe(100);
    expect(wav.length).toBe(44 + 1600 * 2);
  });

  it("keeps at most fifteen seconds", async () => {
    const recorder = new Recorder();
    const start = recorder.start();
    await flush();
    for (let i = 0; i < MAX_SECONDS + 3; i += 1) post(new Float32Array(16_000));
    await start;
    expect(recorder.stop().durationMs).toBe(MAX_SECONDS * 1000);
  });

  it("a resumed context, a refused microphone, a broken worklet, a silent microphone", async () => {
    audio.state = "suspended";
    const resumed = new Recorder();
    const start = resumed.start();
    await flush();
    post(new Float32Array(160));
    await expect(start).resolves.toBeUndefined();
    resumed.cancel();

    getUserMedia.mockRejectedValueOnce(new DOMException("no", "NotAllowedError"));
    await expect(new Recorder().start()).rejects.toMatchObject({ problem: "denied" });
    getUserMedia.mockRejectedValueOnce(new DOMException("none", "NotFoundError"));
    await expect(new Recorder().start()).rejects.toMatchObject({ problem: "no_device" });

    audio.failModule = true;
    const broken = new Recorder().start();
    await expect(broken).rejects.toBeInstanceOf(RecorderError);
    await expect(broken).rejects.toMatchObject({ problem: "failed" });
    expect(track.stop).toHaveBeenCalled(); // released
    audio.failModule = false;

    vi.useFakeTimers();
    const silent = new Recorder().start();
    const failed = expect(silent).rejects.toMatchObject({ problem: "failed" });
    await vi.advanceTimersByTimeAsync(LIVE_TIMEOUT_MS);
    await failed;
  });
});

describe("useCommentaryAudio", () => {
  it("one controller per page, with a player, released when the page goes", () => {
    const { result, rerender, unmount } = renderHook(() => useCommentaryAudio());
    const first = result.current.controller;
    expect(first.player).not.toBeNull();
    expect(result.current.audio).toMatchObject({ on: false, speakingId: null });
    rerender();
    expect(result.current.controller).toBe(first);
    act(() => first.enable());
    expect(result.current.audio.on).toBe(true);
    unmount();
    expect(first.getSnapshot().on).toBe(false); // the page left: silent
  });
});
