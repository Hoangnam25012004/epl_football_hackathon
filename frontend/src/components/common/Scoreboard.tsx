"use client";

// Score and clock come from the server: `snapshot.score`, `snapshot.clock_display`, and between two
// snapshots the minute of the latest event (never a clock ticking in the browser, section 5.2).
import { formatMinute } from "@/lib/clock";
import type { MatchInfo, MatchSnapshot, ScenarioInfo } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import type { MatchEvent, SessionStatus } from "@/lib/ws-types";
import { teamCard } from "@/hooks/useScenarios";
import { TeamChip } from "./ui";

export function matchClock(snapshot: MatchSnapshot | null, latest: MatchEvent | undefined): string {
  if (latest && (!snapshot || latest.seq > snapshot.as_of_seq)) {
    return formatMinute(latest.period, latest.clock_ms);
  }
  return snapshot?.clock_display ?? "0'";
}

const STATUS_TONE: Record<SessionStatus, string> = {
  created: "bg-slate-500/20 text-muted",
  running: "bg-red-600 text-white",
  paused: "bg-amber-500/25 text-amber-700 dark:text-amber-300",
  finished: "bg-slate-500/25 text-ink",
  stopped: "bg-slate-500/25 text-ink",
  error: "bg-red-900 text-red-100",
};

export function Scoreboard({
  match,
  snapshot,
  latest,
  status,
  scenario,
  large = false,
}: {
  match: MatchInfo | null;
  snapshot: MatchSnapshot | null;
  latest: MatchEvent | undefined;
  status: SessionStatus | null;
  scenario: ScenarioInfo | null;
  large?: boolean;
}) {
  const { t } = useT();
  if (!match) return <span className="text-sm text-muted">{t("common.loading")}</span>;
  const home = match.home;
  const away = match.away;
  const score = snapshot?.score ?? {};
  return (
    <div data-testid="scoreboard" className="flex items-center gap-2">
      <TeamChip name={home.short_name} color={teamCard(scenario, home.id)?.primary_color} />
      <span
        data-testid="score"
        className={`tabular font-extrabold ${large ? "text-3xl" : "text-xl"}`}
      >
        {score[home.id] ?? 0}–{score[away.id] ?? 0}
      </span>
      <TeamChip name={away.short_name} color={teamCard(scenario, away.id)?.primary_color} />
      <span data-testid="clock" className="tabular min-w-12 text-center text-sm font-semibold">
        {matchClock(snapshot, latest)}
      </span>
      {status ? (
        <span
          data-testid="match-status"
          data-status={status}
          className={`rounded px-1.5 py-0.5 text-[11px] font-bold uppercase ${STATUS_TONE[status]}`}
        >
          {status === "running" ? "● " : ""}
          {t(`status.${status}`)}
        </span>
      ) : null}
    </div>
  );
}
