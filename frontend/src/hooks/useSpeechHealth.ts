"use client";

// What the server can do with voices (`/healthz`, docs/day5-design.md section 9.4): who reads
// aloud (Azure, the mock, the browser, nobody) and who turns speech into text (P2). Unknown
// until the answer comes: null.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export type SpeechProvider = "azure" | "mock" | "browser" | "off";
export type SttProvider = "azure" | "mock" | "off";
export type SpeechHealth = { tts: SpeechProvider | null; stt: SttProvider | null };

export function useSpeechHealth(): SpeechHealth {
  const [health, setHealth] = useState<SpeechHealth>({ tts: null, stt: null });
  useEffect(() => {
    let alive = true;
    void api.health().then((result) => {
      if (!alive || !result.ok) return;
      const tts = result.data.speech?.provider;
      const stt = result.data.speech?.stt;
      setHealth({
        tts: tts === "azure" || tts === "mock" || tts === "browser" ? tts : "off",
        stt: stt === "azure" || stt === "mock" ? stt : "off",
      });
    });
    return () => {
      alive = false;
    };
  }, []);
  return health;
}
