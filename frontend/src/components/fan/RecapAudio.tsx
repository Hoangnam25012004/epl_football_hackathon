"use client";

// The recap read aloud (docs/day5-design.md section 10.6): asked from the server on the
// listener's click (the recap, then its key moments), played with the browser's own controls
// (seek, pause), "listen again". While it plays the live voice waits. Without a clip of the
// server, the browser's voice reads the same words. A new scope, language, register or voice
// is another recording: the panel gives this component a new `key` (a fresh state).
import { useEffect, useRef, useState } from "react";
import { api, apiBase } from "@/lib/api";
import { browserVoice } from "@/lib/audio/browser-voice";
import type { AudioController } from "@/lib/audio/controller";
import type { RecapView, SpeechCue } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import type { Lang, Mode, VoiceGender } from "@/lib/profile";

type Phase = "idle" | "loading" | "ready" | "browser" | "error";

export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function RecapAudio({
  matchId,
  recap,
  language,
  mode,
  gender,
  controller,
}: {
  matchId: string;
  recap: RecapView;
  language: Lang;
  mode: Mode;
  gender: VoiceGender;
  controller: AudioController | null;
}) {
  const { t } = useT();
  const [phase, setPhase] = useState<Phase>("idle");
  const [cue, setCue] = useState<SpeechCue | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [voice] = useState(browserVoice);

  useEffect(
    () => () => {
      voice.cancel();
      controller?.dispatch({ type: "recapEnd" });
    },
    [voice, controller],
  );

  if (language !== "vi") return null;

  const load = async () => {
    setPhase("loading");
    const result = await api.recap(matchId, recap.scope, language, mode, gender);
    const spoken = result.ok ? result.data.speech : null;
    if (!result.ok || !spoken || spoken.note === "guard" || spoken.note === "speed") {
      setPhase("error");
      return;
    }
    setCue(spoken);
    setRevision(result.data.revision);
    setPhase(spoken.audio ? "ready" : "browser");
  };

  const readWithBrowser = () => {
    if (!cue) return;
    controller?.dispatch({ type: "recapStart" });
    voice.speak(cue, {
      onend: () => controller?.dispatch({ type: "recapEnd" }),
      onerror: () => {
        controller?.dispatch({ type: "recapEnd" });
        setPhase("error");
      },
    });
  };

  const again = () => {
    const element = audio.current;
    if (!element) return;
    element.currentTime = 0;
    void element.play();
  };

  const stale = revision !== null && recap.revision > revision;
  return (
    <div className="flex flex-col gap-2" data-testid="recap-audio" data-phase={phase}>
      {phase === "idle" || phase === "loading" ? (
        <button
          type="button"
          data-testid="recap-listen"
          onClick={() => void load()}
          disabled={phase === "loading"}
          className="self-start rounded-full bg-accent-fill px-4 py-2 text-sm font-bold text-white disabled:opacity-60"
        >
          {phase === "loading" ? t("fan.recapPreparing") : `▶ ${t("fan.recapListen")}`}
        </button>
      ) : null}
      {phase === "ready" && cue?.audio ? (
        <div className="flex flex-wrap items-center gap-2">
          <audio
            ref={audio}
            data-testid="recap-player"
            data-duration-ms={cue.audio.duration_ms}
            src={apiBase() + cue.audio.url}
            controls
            autoPlay
            preload="none"
            onPlay={() => controller?.dispatch({ type: "recapStart" })}
            onPause={() => controller?.dispatch({ type: "recapEnd" })}
            onEnded={() => controller?.dispatch({ type: "recapEnd" })}
            onError={() => setPhase("error")}
            className="max-w-full"
          />
          <span className="tabular text-xs text-muted">
            ≈ {formatDuration(cue.audio.duration_ms)}
          </span>
          <button
            type="button"
            data-testid="recap-replay"
            onClick={again}
            className="rounded-full border border-line px-3 py-1 text-xs"
          >
            ↺ {t("fan.recapReplay")}
          </button>
        </div>
      ) : null}
      {phase === "browser" ? (
        voice.available("vi-VN", gender) ? (
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="recap-browser"
              onClick={readWithBrowser}
              className="rounded-full border border-line px-3 py-1.5 text-sm"
            >
              ▶ {t("fan.recapBrowser")}
            </button>
            <button
              type="button"
              onClick={() => {
                voice.cancel();
                controller?.dispatch({ type: "recapEnd" });
              }}
              className="rounded-full border border-line px-3 py-1.5 text-sm"
            >
              ■ {t("fan.recapStop")}
            </button>
          </div>
        ) : (
          <p className="text-sm text-muted">{t("fan.voiceNoBrowser")}</p>
        )
      ) : null}
      {phase === "error" ? (
        <p className="text-sm text-muted" data-testid="recap-audio-error">
          {t("fan.recapAudioError")}
        </p>
      ) : null}
      {stale ? (
        <button
          type="button"
          data-testid="recap-update"
          onClick={() => void load()}
          className="self-start rounded border border-accent/50 px-2 py-1 text-xs text-accent"
        >
          {t("fan.recapNewAudio")}
        </button>
      ) : null}
    </div>
  );
}
