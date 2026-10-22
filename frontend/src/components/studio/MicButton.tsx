"use client";

// Speak a claim (P2, docs/day5-design.md section 11): press to record, press again to stop (or
// 15 s, or Esc to cancel); the server's text goes into the claim box and nothing is sent: the
// commentator reads it and presses Enter. Shown only when the server has speech to text and the
// page can record (a secure context with a microphone API). The recorder loads on first use.
// "Recording" shows only once the microphone delivers sound ("starting" until then): words said
// earlier would be lost.
import { useEffect, useEffectEvent, useRef, useState, useSyncExternalStore } from "react";
import { useToast } from "@/components/common/Toast";
import { useSpeechHealth } from "@/hooks/useSpeechHealth";
import { api } from "@/lib/api";
import type { Recorder, RecorderProblem } from "@/lib/audio/recorder";
import { useT, type Key } from "@/lib/i18n";

export const MAX_SECONDS = 15; // STT_MAX_SECONDS of the server (default)
const TICK_MS = 250;

/** Can this page record at all? (A secure context, a microphone API, AudioWorklet.) */
export function microphoneSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof AudioWorkletNode !== "undefined"
  );
}

const noSubscription = () => () => {};

type Phase = "idle" | "starting" | "recording" | "sending";

const PROBLEM_KEYS: Record<RecorderProblem, Key> = {
  denied: "studio.micDenied",
  no_device: "studio.micNoDevice",
  failed: "studio.micError",
};

export function MicButton({
  language,
  onText,
  load = () => import("@/lib/audio/recorder"),
}: {
  language: "vi" | "en";
  onText: (text: string) => void;
  load?: () => Promise<{ Recorder: new () => Recorder }>; // tests give a fake recorder
}) {
  const { t } = useT();
  const toast = useToast();
  const { stt } = useSpeechHealth();
  const supported = useSyncExternalStore(noSubscription, microphoneSupported, () => false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [missing, setMissing] = useState(false); // no microphone on this machine
  const recorder = useRef<Recorder | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);
  const attempt = useRef(0); // a start still opening the microphone is abandoned by a new value

  const stopTicking = () => {
    if (ticker.current !== null) clearInterval(ticker.current);
    ticker.current = null;
  };

  const cancel = () => {
    attempt.current += 1;
    stopTicking();
    recorder.current?.cancel();
    recorder.current = null;
    setPhase("idle");
  };

  const finish = async () => {
    const active = recorder.current;
    if (!active) return;
    stopTicking();
    recorder.current = null;
    setPhase("sending");
    const recording = active.stop();
    const result = await api.recognize(new Blob([recording.wav], { type: "audio/wav" }), language);
    setPhase("idle");
    if (!result.ok) {
      toast.show(t(result.error.status === 429 ? "studio.micBusy" : "studio.micError"), "error");
      return;
    }
    if (result.data.status === "ok" && result.data.text.trim()) {
      onText(result.data.text.trim());
      toast.show(t("studio.micFilled"));
    } else {
      toast.show(t("studio.micNoMatch"));
    }
  };

  const start = async () => {
    const mine = ++attempt.current;
    setPhase("starting");
    setSeconds(0);
    let next: Recorder;
    try {
      const { Recorder } = await load();
      next = new Recorder();
      await next.start();
    } catch (error) {
      if (mine !== attempt.current) return; // cancelled meanwhile
      setPhase("idle");
      const problem = (error as { problem?: RecorderProblem }).problem ?? "failed";
      if (problem === "no_device") setMissing(true);
      toast.show(t(PROBLEM_KEYS[problem]), "error");
      return;
    }
    if (mine !== attempt.current) {
      next.cancel(); // Esc, or the page left, while the microphone opened: close it
      return;
    }
    recorder.current = next;
    setPhase("recording");
    const began = Date.now();
    ticker.current = setInterval(() => {
      const elapsed = (Date.now() - began) / 1000;
      setSeconds(Math.floor(elapsed));
      if (elapsed >= MAX_SECONDS) void finish();
    }, TICK_MS);
  };

  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.key === "Escape" && (phase === "recording" || phase === "starting")) {
      event.preventDefault();
      cancel();
    }
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKey(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
  // leaving the page stops the microphone
  useEffect(
    () => () => {
      attempt.current += 1;
      if (ticker.current !== null) clearInterval(ticker.current);
      recorder.current?.cancel();
    },
    [],
  );

  if (!supported || missing || stt === null || stt === "off") return null;
  const recording = phase === "recording";
  return (
    <>
      <button
        type="button"
        data-testid="mic-button"
        data-phase={phase}
        aria-pressed={recording}
        aria-label={recording ? t("studio.micStop") : t("studio.mic")}
        title={recording ? t("studio.micStop") : t("studio.mic")}
        disabled={phase === "sending" || phase === "starting"}
        aria-busy={phase === "starting" || phase === "sending"}
        onClick={() => void (recording ? finish() : start())}
        className={`flex items-center gap-1 rounded border px-3 py-2 text-sm font-semibold disabled:opacity-50 ${
          recording ? "border-verdict-false text-verdict-false" : "border-line hover:bg-panel-2"
        }`}
      >
        {recording ? (
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full bg-verdict-false" />
        ) : (
          <span aria-hidden>🎤</span>
        )}
        {recording ? `${seconds}s` : null}
      </button>
      <span role="status" className="sr-only" data-testid="mic-status">
        {recording
          ? t("studio.micRecording", { s: seconds })
          : phase === "starting"
            ? t("studio.micStarting")
            : phase === "sending"
              ? t("studio.micSending")
              : ""}
      </span>
    </>
  );
}
