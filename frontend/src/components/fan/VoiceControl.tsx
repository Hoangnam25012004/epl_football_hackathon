"use client";

// The voice of the fan player (docs/day5-design.md section 10.5): on / off (a tap: browsers only
// play sound after one), female or male, where the voice comes from, skip the line being read.
// The voice is AI-generated and reads synthetic data, and the control says so. Keys: V turns the
// voice on or off, N skips the line (not while typing).
import { useEffect, useEffectEvent } from "react";
import type { SpeechProvider } from "@/hooks/useSpeechHealth";
import type { AudioSnapshot } from "@/lib/audio/controller";
import { useT } from "@/lib/i18n";
import type { Lang, VoiceGender } from "@/lib/profile";

export type { SpeechProvider };

/** A key typed into a field (or with a modifier) is not a shortcut. */
export function isShortcut(event: KeyboardEvent): boolean {
  if (event.altKey || event.ctrlKey || event.metaKey || event.repeat) return false;
  const target = event.target as HTMLElement | null;
  if (!target || typeof target.closest !== "function") return true;
  return !target.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])");
}

export function voiceLabelKey(
  provider: SpeechProvider | null,
  audio: AudioSnapshot,
):
  | "fan.voiceTap"
  | "fan.voiceBrowser"
  | "fan.voiceServerAzure"
  | "fan.voiceServerMock"
  | "fan.voiceNoBrowser"
  | "fan.voiceUnavailable" {
  if (audio.needsGesture) return "fan.voiceTap";
  if (audio.source === "browser" || audio.browserOnly) return "fan.voiceBrowser";
  if (provider === "azure") return "fan.voiceServerAzure";
  if (provider === "mock") return "fan.voiceServerMock";
  if (provider === "browser")
    return audio.browserAvailable ? "fan.voiceBrowser" : "fan.voiceNoBrowser";
  return "fan.voiceUnavailable";
}

export function VoiceControl({
  on,
  resume,
  gender,
  language,
  provider,
  audio,
  onToggle,
  onGender,
  onSkip,
}: {
  on: boolean;
  resume: boolean; // the voice was on when the page was left: one tap brings it back
  gender: VoiceGender;
  language: Lang;
  provider: SpeechProvider | null;
  audio: AudioSnapshot;
  onToggle: () => void;
  onGender: (gender: VoiceGender) => void;
  onSkip: () => void;
}) {
  const { t } = useT();
  const usable = language === "vi" && provider !== "off";
  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (!isShortcut(event)) return;
    const key = event.key.toLowerCase();
    if (key === "v" && (usable || on)) {
      event.preventDefault();
      onToggle(); // a key press is a user gesture: it unlocks audio like a tap
    } else if (key === "n" && on && audio.speakingId) {
      event.preventDefault();
      onSkip();
    }
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKey(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);

  if (audio.needsGesture || (resume && !on)) {
    return (
      <button
        type="button"
        data-testid="voice-tap"
        onClick={onToggle}
        className="rounded-full bg-accent-fill px-3 py-1.5 text-sm font-bold text-white"
      >
        🔊 {t(audio.needsGesture ? "fan.voiceTap" : "fan.voiceResume")}
      </button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="voice-control">
      <button
        type="button"
        data-testid="voice-toggle"
        aria-pressed={on}
        aria-keyshortcuts="V"
        disabled={!usable && !on}
        title={usable ? undefined : t("fan.voiceOnlyVi")}
        onClick={onToggle}
        className={`rounded-full border px-3 py-1.5 text-sm font-semibold disabled:opacity-60 ${
          on ? "border-accent bg-accent/10" : "border-line"
        }`}
      >
        {on ? `🔊 ${t("fan.voiceOff")}` : `🔇 ${t("fan.voiceOn")}`}
      </button>
      {on ? (
        <>
          <div role="radiogroup" aria-label={t("fan.voice")} className="flex gap-1">
            {(["female", "male"] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={gender === value}
                data-testid={`voice-${value}`}
                onClick={() => onGender(value)}
                className={`rounded-full border px-2 py-1 text-xs ${
                  gender === value ? "border-accent bg-accent/10 font-bold" : "border-line"
                }`}
              >
                {t(value === "female" ? "fan.voiceFemale" : "fan.voiceMale")}
              </button>
            ))}
          </div>
          <span
            data-testid="voice-status"
            className="rounded-full border border-line px-2 py-0.5 text-xs text-muted"
          >
            {t(voiceLabelKey(provider, audio))}
          </span>
          <button
            type="button"
            data-testid="voice-skip"
            aria-keyshortcuts="N"
            onClick={onSkip}
            disabled={!audio.speakingId}
            className="rounded-full border border-line px-2 py-1 text-xs disabled:opacity-50"
          >
            ⏭ {t("fan.voiceSkip")}
          </button>
          <span className="w-full text-xs text-muted">{t("fan.voiceAiNote")}</span>
        </>
      ) : null}
    </div>
  );
}
