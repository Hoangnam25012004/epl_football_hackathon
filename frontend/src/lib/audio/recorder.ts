// The studio's microphone for a spoken claim (P2, docs/day5-design.md section 11): mono PCM at
// 16 kHz, written as the WAV the server checks. Loaded only when the microphone is first used,
// so the studio's first load does not grow. A browser that ignores the rate asked records at its
// own rate; the samples are then resampled here (linear interpolation).
export const STT_RATE = 16_000;
export const MAX_SECONDS = 15;
export const WORKLET_URL = "/worklets/pcm-capture.js"; // not under /audio: the clips' route
// A microphone can take over a second to deliver its first samples (measured: 1.4 s on first
// use): `start` waits for them, so "recording" is shown only when the words are captured.
export const LIVE_TIMEOUT_MS = 4_000;

export type RecorderProblem = "denied" | "no_device" | "failed";

export class RecorderError extends Error {
  constructor(readonly problem: RecorderProblem) {
    super(problem);
  }
}

/** What getUserMedia or the audio graph threw, as something the listener can act on. */
export function problemOf(error: unknown): RecorderProblem {
  const name = error instanceof Error || error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "denied";
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "NotReadableError") {
    return "no_device";
  }
  return "failed";
}

/** Samples from the context's rate to 16 kHz. */
export function resample(input: Float32Array, from: number, to = STT_RATE): Float32Array {
  if (from === to || input.length === 0) return input;
  const length = Math.floor((input.length * to) / from);
  const output = new Float32Array(length);
  const ratio = from / to;
  for (let index = 0; index < length; index += 1) {
    const at = index * ratio;
    const left = Math.floor(at);
    const right = Math.min(left + 1, input.length - 1);
    const weight = at - left;
    output[index] = (input[left] ?? 0) * (1 - weight) + (input[right] ?? 0) * weight;
  }
  return output;
}

/** The blocks of the worklet as one buffer, cut at `max` samples. */
export function joinChunks(chunks: readonly Float32Array[], max: number): Float32Array {
  const total = Math.min(
    max,
    chunks.reduce((sum, chunk) => sum + chunk.length, 0),
  );
  const joined = new Float32Array(total);
  let at = 0;
  for (const chunk of chunks) {
    if (at >= total) break;
    const part = chunk.subarray(0, total - at);
    joined.set(part, at);
    at += part.length;
  }
  return joined;
}

/** A PCM 16-bit mono WAV of samples in [-1, 1]. */
export function encodeWav(samples: Float32Array, rate = STT_RATE): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const text = (at: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(at + index, value.charCodeAt(index));
    }
  };
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index] ?? 0));
    view.setInt16(44 + index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return bytes;
}

export type Recording = { wav: Uint8Array<ArrayBuffer>; durationMs: number };

export class Recorder {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private nodes: AudioNode[] = []; // held: a source nobody references may be collected
  private chunks: Float32Array[] = [];
  private samples = 0;
  private rate = STT_RATE;
  private live: (() => void) | null = null;

  /** Ask for the microphone and resolve once it delivers sound; RecorderError when it cannot
   * (or when nothing comes within LIVE_TIMEOUT_MS). */
  async start(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (error) {
      throw new RecorderError(problemOf(error));
    }
    try {
      try {
        this.context = new AudioContext({ sampleRate: STT_RATE });
      } catch {
        this.context = new AudioContext(); // its own rate: resampled at the end
      }
      const context = this.context;
      this.rate = context.sampleRate;
      await context.audioWorklet.addModule(WORKLET_URL);
      const source = context.createMediaStreamSource(this.stream);
      const capture = new AudioWorkletNode(context, "pcm-capture");
      const silent = context.createGain(); // pulls the graph without playing the microphone
      silent.gain.value = 0;
      capture.port.onmessage = (event: MessageEvent<Float32Array>) => this.add(event.data);
      source.connect(capture);
      capture.connect(silent);
      silent.connect(context.destination);
      this.nodes = [source, capture, silent];
      const live = new Promise<void>((resolve) => (this.live = resolve));
      if (context.state === "suspended") await context.resume();
      let timer: ReturnType<typeof setTimeout> | undefined;
      const late = new Promise<"late">((resolve) => {
        timer = setTimeout(() => resolve("late"), LIVE_TIMEOUT_MS);
      });
      const first = await Promise.race([live.then(() => "live" as const), late]);
      clearTimeout(timer);
      if (first === "late") throw new Error("no sound from the microphone");
    } catch (error) {
      this.release();
      throw new RecorderError(problemOf(error) === "denied" ? "denied" : "failed");
    }
  }

  private add(chunk: Float32Array): void {
    this.live?.();
    this.live = null;
    const max = MAX_SECONDS * this.rate;
    if (this.samples >= max) return;
    this.chunks.push(chunk);
    this.samples += chunk.length;
  }

  get elapsedMs(): number {
    return (this.samples / this.rate) * 1000;
  }

  /** Stop and return the WAV (at most MAX_SECONDS, 16 kHz). */
  stop(): Recording {
    const joined = joinChunks(this.chunks, MAX_SECONDS * this.rate);
    const rate = this.rate;
    this.release();
    const samples = resample(joined, rate).subarray(0, MAX_SECONDS * STT_RATE);
    return {
      wav: encodeWav(samples),
      durationMs: Math.round((samples.length / STT_RATE) * 1000),
    };
  }

  /** Stop without a result (Esc, leaving the page). */
  cancel(): void {
    this.release();
  }

  private release(): void {
    this.live = null;
    this.nodes.forEach((node) => node.disconnect());
    this.nodes = [];
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    const context = this.context;
    this.context = null;
    if (context && context.state !== "closed") void context.close().catch(() => {});
    this.chunks = [];
    this.samples = 0;
  }
}
