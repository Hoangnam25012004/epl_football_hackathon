// The studio's microphone (P2, docs/day5-design.md section 11): the WAV the server checks, the
// resampling when the browser ignores 16 kHz, the cut at 15 s, and the problems a listener sees.
import { describe, expect, it } from "vitest";
import {
  MAX_SECONDS,
  STT_RATE,
  encodeWav,
  joinChunks,
  problemOf,
  resample,
} from "@/lib/audio/recorder";

describe("recorder", () => {
  it("writes a PCM 16 kHz mono 16-bit WAV, clamping the samples", () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 2, -2, 0.5]));
    const view = new DataView(wav.buffer);
    const text = (at: number) => String.fromCharCode(...wav.subarray(at, at + 4));
    expect([text(0), text(8), text(12), text(36)]).toEqual(["RIFF", "WAVE", "fmt ", "data"]);
    expect(view.getUint32(4, true)).toBe(36 + 12);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(STT_RATE);
    expect(view.getUint32(28, true)).toBe(STT_RATE * 2);
    expect(view.getUint16(32, true)).toBe(2);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(12);
    const samples = Array.from({ length: 6 }, (_, i) => view.getInt16(44 + i * 2, true));
    expect(samples).toEqual([0, 32767, -32768, 32767, -32768, 16383]);
  });

  it("resamples 48 kHz to 16 kHz by linear interpolation", () => {
    const ramp = Float32Array.from({ length: 480 }, (_, i) => i / 480);
    const out = resample(ramp, 48_000);
    expect(out.length).toBe(160);
    out.forEach((value, i) => expect(value).toBeCloseTo((i * 3) / 480, 6));
    const odd = resample(
      Float32Array.from({ length: 441 }, () => 0.25),
      44_100,
    );
    expect(odd.length).toBe(160);
    expect(odd.every((value) => Math.abs(value - 0.25) < 1e-6)).toBe(true);
    const same = new Float32Array([0.1, 0.2]);
    expect(resample(same, STT_RATE)).toBe(same);
    expect(resample(new Float32Array(0), 48_000)).toHaveLength(0);
  });

  it("joins the worklet's blocks and cuts at the limit", () => {
    const blocks = [new Float32Array([1, 2, 3]), new Float32Array([4, 5]), new Float32Array([6])];
    expect(Array.from(joinChunks(blocks, 100))).toEqual([1, 2, 3, 4, 5, 6]);
    expect(Array.from(joinChunks(blocks, 4))).toEqual([1, 2, 3, 4]);
    expect(joinChunks([], 10)).toHaveLength(0);
    // fifteen seconds at 16 kHz is exactly what the server accepts
    expect(MAX_SECONDS * STT_RATE).toBe(240_000);
  });

  it("tells the listener what went wrong", () => {
    expect(problemOf(new DOMException("no", "NotAllowedError"))).toBe("denied");
    expect(problemOf(new DOMException("no", "SecurityError"))).toBe("denied");
    expect(problemOf(new DOMException("none", "NotFoundError"))).toBe("no_device");
    expect(problemOf(new DOMException("busy", "NotReadableError"))).toBe("no_device");
    expect(problemOf(new Error("boom"))).toBe("failed");
    expect(problemOf("weird")).toBe("failed");
  });
});
