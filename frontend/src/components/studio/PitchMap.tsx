"use client";

// Where the evidence happened (docs/day4-design.md section 5.9): events on a 105 x 68 pitch, the
// home team attacking to the right, the away team rotated (lib/pitch.ts).
import type { PlayerBrief, ScenarioInfo } from "@/lib/contracts";
import { formatMinute } from "@/lib/clock";
import { safeColor } from "@/lib/color";
import { kindLabel, useT } from "@/lib/i18n";
import { PITCH_LENGTH, PITCH_WIDTH, toPitch } from "@/lib/pitch";
import type { MatchEvent } from "@/lib/ws-types";
import { teamCard } from "@/hooks/useScenarios";
import { playerName } from "@/hooks/usePlayers";

function Mark({ type, x, y, color }: { type: string; x: number; y: number; color: string }) {
  if (type === "goal") {
    return <circle cx={x} cy={y} r={2.2} fill={color} stroke="#fff" strokeWidth={0.8} />;
  }
  if (type === "shot") {
    return <circle cx={x} cy={y} r={1.6} fill="none" stroke={color} strokeWidth={0.8} />;
  }
  return <rect x={x - 0.9} y={y - 0.9} width={1.8} height={1.8} fill={color} opacity={0.85} />;
}

export function PitchMap({
  events,
  homeId,
  scenario,
  players,
}: {
  events: MatchEvent[];
  homeId: string;
  scenario: ScenarioInfo | null;
  players: Map<string, PlayerBrief>;
}) {
  const { t, lang } = useT();
  const points = events
    .map((event) => ({ event, point: toPitch(event, homeId) }))
    .filter((p): p is { event: MatchEvent; point: { x: number; y: number } } => p.point !== null);
  return (
    <svg
      viewBox={`-2 -2 ${PITCH_LENGTH + 4} ${PITCH_WIDTH + 4}`}
      className="w-full rounded bg-emerald-800"
      role="img"
      aria-label={t("drawer.pitch")}
      data-testid="pitch-map"
    >
      <g fill="none" stroke="#ffffff" strokeOpacity={0.55} strokeWidth={0.4}>
        <rect x={0} y={0} width={PITCH_LENGTH} height={PITCH_WIDTH} />
        <line x1={PITCH_LENGTH / 2} x2={PITCH_LENGTH / 2} y1={0} y2={PITCH_WIDTH} />
        <circle cx={PITCH_LENGTH / 2} cy={PITCH_WIDTH / 2} r={9.15} />
        <rect x={0} y={13.84} width={16.5} height={40.32} />
        <rect x={PITCH_LENGTH - 16.5} y={13.84} width={16.5} height={40.32} />
        <rect x={0} y={24.84} width={5.5} height={18.32} />
        <rect x={PITCH_LENGTH - 5.5} y={24.84} width={5.5} height={18.32} />
      </g>
      {points.map(({ event, point }) => (
        <g key={event.id} data-testid="pitch-point">
          <title>
            {`${formatMinute(event.period, event.clock_ms)} · ${kindLabel(lang, event.type ?? "")}${
              event.player ? ` · ${playerName(players, event.player)}` : ""
            }`}
          </title>
          <Mark
            type={event.type ?? ""}
            x={point.x}
            y={point.y}
            color={safeColor(teamCard(scenario, event.team)?.primary_color)}
          />
        </g>
      ))}
    </svg>
  );
}
