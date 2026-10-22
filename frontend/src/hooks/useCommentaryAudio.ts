"use client";

// The fan player's voice for React (docs/day5-design.md section 10): one AudioController per
// page, kept across reconnections (a new hello resets what was waiting, never the voice itself).
import { useEffect, useState, useSyncExternalStore } from "react";
import { apiBase } from "@/lib/api";
import { browserVoice } from "@/lib/audio/browser-voice";
import { AudioController, type AudioSnapshot } from "@/lib/audio/controller";
import { AudioPlayer, type ElementLike } from "@/lib/audio/player";

function createController(): AudioController {
  const controller = new AudioController();
  if (typeof window === "undefined") return controller;
  const voice = browserVoice();
  const element = typeof Audio === "undefined" ? null : (new Audio() as unknown as ElementLike);
  controller.player = new AudioPlayer(element, voice, apiBase(), {
    ended: controller.ended,
    failed: controller.failed,
    blocked: controller.blocked,
  });
  void voice.ready().then(() => controller.setBrowserAvailable(voice.available("vi-VN")));
  return controller;
}

export function useCommentaryAudio(): { controller: AudioController; audio: AudioSnapshot } {
  const [controller] = useState(createController);
  // leaving the page silences it; the controller stays usable (React's strict mode runs this
  // cleanup once on mount, and the next tap turns the voice on again)
  useEffect(() => () => controller.release(), [controller]);
  const audio = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  return { controller, audio };
}
