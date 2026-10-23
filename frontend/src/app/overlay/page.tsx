"use client";

// /overlay/?match=ID&lang=en|vi[&debug=1]: the transparent lower-third page for OBS / vMix
// (docs/day4-design.md section 6). No chrome, no interaction; the current item is also published
// as JSON (#pundit-on-air, window.punditOverlay) with its match time ("timed, machine-readable").
import { useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { PageShell } from "@/components/common/PageShell";
import { Stage } from "@/components/overlay/Stage";
import type { LowerThirdView, OnAirState } from "@/lib/contracts";
import { useMatchConnection } from "@/hooks/useMatchConnection";
import { useOverlaySchedule } from "@/hooks/useOverlaySchedule";
import type { SchedulerState } from "@/lib/overlay-scheduler";

/** What is on air, as JSON with its match time (the rules' "timed, machine-readable" output). */
function publishedState(schedule: SchedulerState) {
  return {
    current: schedule.current?.item ?? null,
    queue: schedule.queue.map((item) => item.id),
    on_air: schedule.onAir,
    // wall-clock ms when the item went on air (the scheduler keeps performance.now() times)
    shown_at: schedule.current ? Math.round(performance.timeOrigin + schedule.current.since) : null,
  };
}

declare global {
  interface Window {
    punditOverlay?: {
      current: LowerThirdView | null;
      queue: string[];
      on_air: OnAirState | null;
      shown_at: number | null;
    };
  }
}

export default function OverlayPage() {
  return (
    <PageShell defaultLang="en" transparent>
      <Overlay />
    </PageShell>
  );
}

function Overlay() {
  const params = useSearchParams();
  const matchId = params.get("match");
  const language = params.get("lang") === "vi" ? "vi" : "en";
  const debug = params.get("debug") === "1";
  const connection = useMatchConnection(matchId, `mode=analyst&lang=${language}`, {
    name: "overlay",
    unknownMatch: "wait",
  });
  const schedule = useOverlaySchedule(connection, matchId, language);
  const current = schedule.current?.item ?? null;

  const published = publishedState(schedule);
  useEffect(() => {
    window.punditOverlay = publishedState(schedule);
  }, [schedule]);

  // "<" escaped: the JSON can never close the script element
  const json = JSON.stringify(published).replace(/</g, "\\u003c");
  return (
    <div
      className="h-dvh w-dvw"
      data-testid="overlay-root"
      data-connection={connection.socketState.status}
    >
      <Stage current={current} debug={debug} />
      <script
        id="pundit-on-air"
        type="application/json"
        dangerouslySetInnerHTML={{ __html: json }}
      />
      {debug ? (
        // focusable: it scrolls, and the keyboard must reach it too (axe, Day 6)
        <pre
          tabIndex={0}
          aria-label="On-air state (debug)"
          className="fixed right-2 top-2 max-h-[40vh] max-w-[40vw] overflow-auto rounded bg-black/80 p-2 text-[10px] text-green-300"
        >
          {connection.socketState.status} · {JSON.stringify(published, null, 1)}
        </pre>
      ) : null}
    </div>
  );
}
