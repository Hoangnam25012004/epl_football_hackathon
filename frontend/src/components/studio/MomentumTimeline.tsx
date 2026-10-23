"use client";

// Momentum per played minute (docs/day4-design.md sections 3.3, 5.8): the server's values drawn
// as an area (home above the line), with its markers (goals, VAR, red cards, half-time).
import { useEffect, useId, useState } from "react";
import { EmptyState, LoadFailed } from "@/components/common/ui";
import { api } from "@/lib/api";
import { safeColor } from "@/lib/color";
import type { ScenarioInfo, TimelineView } from "@/lib/contracts";
import { kindLabel, useT } from "@/lib/i18n";
import { teamCard } from "@/hooks/useScenarios";
import { useThrottled } from "@/hooks/useThrottled";

const WIDTH = 600;
const HEIGHT = 140;
const REFRESH_MS = 2_000;
const ICON: Record<string, string> = {
  goal: "⚽",
  goal_overturned: "▣",
  red_card: "▮",
  period_end: "|",
  full_time: "■",
};

export function MomentumTimeline({
  matchId,
  version,
  scenario,
}: {
  matchId: string;
  version: number;
  scenario: ScenarioInfo | null;
}) {
  const { t, lang } = useT();
  const [view, setView] = useState<TimelineView | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const clip = useId(); // unique clip ids: two charts on a page never share them

  const refresh = useThrottled(version, REFRESH_MS);
  useEffect(() => {
    let alive = true;
    void api.timeline(matchId).then((result) => {
      if (!alive) return;
      setFailed(!result.ok); // a failed refresh keeps the last chart
      if (result.ok) setView(result.data);
    });
    return () => {
      alive = false;
    };
  }, [matchId, refresh, attempt]);

  if (!view && failed) return <LoadFailed onRetry={() => setAttempt((n) => n + 1)} />;
  if (!view || view.points.length === 0) {
    return <EmptyState>{view ? t("studio.notEnoughData") : t("common.loading")}</EmptyState>;
  }
  const lastMinute = Math.max(95, view.points[view.points.length - 1]?.minute ?? 95);
  const x = (minute: number) => (minute / lastMinute) * WIDTH;
  const y = (value: number) => HEIGHT / 2 - (value * HEIGHT) / 2;
  const line = view.points
    .map((p) => `${x(p.minute).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join(" ");
  const first = view.points[0]!;
  const last = view.points[view.points.length - 1]!;
  const area = `${x(first.minute)},${HEIGHT / 2} ${line} ${x(last.minute)},${HEIGHT / 2}`;
  const home = safeColor(teamCard(scenario, view.home)?.primary_color);
  const away = safeColor(teamCard(scenario, view.away)?.primary_color);
  const hovered =
    hover === null
      ? null
      : view.points.reduce((best, p) =>
          Math.abs(p.minute - hover) < Math.abs(best.minute - hover) ? p : best,
        );

  return (
    <figure className="p-2" data-testid="momentum-timeline">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-36 w-full"
        role="img"
        aria-label={t("studio.momentum")}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          setHover(((event.clientX - box.left) / box.width) * lastMinute);
        }}
      >
        <defs>
          <clipPath id={`${clip}above`}>
            <rect x="0" y="0" width={WIDTH} height={HEIGHT / 2} />
          </clipPath>
          <clipPath id={`${clip}below`}>
            <rect x="0" y={HEIGHT / 2} width={WIDTH} height={HEIGHT / 2} />
          </clipPath>
        </defs>
        <polygon points={area} fill={home} opacity="0.75" clipPath={`url(#${clip}above)`} />
        <polygon points={area} fill={away} opacity="0.75" clipPath={`url(#${clip}below)`} />
        <line
          x1="0"
          x2={WIDTH}
          y1={HEIGHT / 2}
          y2={HEIGHT / 2}
          stroke="currentColor"
          strokeOpacity="0.4"
        />
        {view.half_time_minute !== null ? (
          <g>
            <line
              x1={x(view.half_time_minute)}
              x2={x(view.half_time_minute)}
              y1="0"
              y2={HEIGHT}
              stroke="currentColor"
              strokeDasharray="3 3"
              strokeOpacity="0.5"
            />
            <text
              x={x(view.half_time_minute) + 3}
              y="11"
              fontSize="10"
              fill="currentColor"
              opacity="0.7"
            >
              {t("studio.halfTime")}
            </text>
          </g>
        ) : null}
        {view.markers
          .filter((m) => m.kind !== "period_end" && m.kind !== "full_time")
          .map((marker) => (
            <g key={marker.moment_id}>
              <title>{`${marker.display_minute} ${kindLabel(lang, marker.kind)}`}</title>
              <line
                x1={x(marker.minute)}
                x2={x(marker.minute)}
                y1="14"
                y2={HEIGHT}
                stroke="currentColor"
                strokeOpacity="0.35"
              />
              <text x={x(marker.minute)} y="12" fontSize="11" textAnchor="middle">
                {ICON[marker.kind] ?? "•"}
              </text>
            </g>
          ))}
        {hovered ? (
          <line
            x1={x(hovered.minute)}
            x2={x(hovered.minute)}
            y1="0"
            y2={HEIGHT}
            stroke="currentColor"
          />
        ) : null}
      </svg>
      <figcaption className="flex justify-between text-[11px] text-muted">
        <span>{t("studio.momentumNote")}</span>
        {hovered ? (
          <span className="tabular font-semibold text-ink">
            {hovered.minute}&apos; · {hovered.display}
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}
