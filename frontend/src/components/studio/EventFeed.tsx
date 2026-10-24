"use client";

// The live event feed (docs/day4-design.md section 5.3). A hello carries no events, so after each
// (re)connection the last 100 are fetched once and merged with the live ones by id.
import { useEffect, useMemo, useState } from "react";
import { EmptyState, Segmented, TeamChip } from "@/components/common/ui";
import { api } from "@/lib/api";
import { formatMinute } from "@/lib/clock";
import type { ScenarioInfo } from "@/lib/contracts";
import { EVENT_ICONS, eventDetail, eventType, isKeyEvent } from "@/lib/events";
import { hasKey, useT, type Key } from "@/lib/i18n";
import type { MatchEvent } from "@/lib/ws-types";
import { teamCard } from "@/hooks/useScenarios";
import { playerName } from "@/hooks/usePlayers";
import type { PlayerBrief } from "@/lib/contracts";

const SHOWN = 150;

export function EventFeed({
  matchId,
  live,
  helloCount,
  scenario,
  players,
}: {
  matchId: string;
  live: MatchEvent[];
  helloCount: number;
  scenario: ScenarioInfo | null;
  players: Map<string, PlayerBrief>;
}) {
  const { t } = useT();
  const [filter, setFilter] = useState<"all" | "key">("all");
  const [backfill, setBackfill] = useState<MatchEvent[]>([]);
  // "blocked" / "yellow" / "stands" in the page's language; a value with no word, as it is
  const detailText = (detail: string) => {
    const key = `detail.${detail}`;
    return hasKey(key) ? t(key) : detail;
  };

  useEffect(() => {
    if (helloCount === 0) return;
    let alive = true;
    void api.lastEvents(matchId, 100).then((result) => {
      if (alive && result.ok) setBackfill(result.data.events as MatchEvent[]);
    });
    return () => {
      alive = false;
    };
  }, [matchId, helloCount]);

  const rows = useMemo(() => {
    const byId = new Map<string, MatchEvent>();
    for (const event of backfill) byId.set(event.id, event);
    for (const event of live) byId.set(event.id, event);
    const merged = [...byId.values()].sort((a, b) => b.seq - a.seq);
    return (filter === "key" ? merged.filter(isKeyEvent) : merged).slice(0, SHOWN);
  }, [backfill, live, filter]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line p-2">
        <Segmented<"all" | "key">
          label={t("studio.events")}
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: t("studio.filterAll") },
            { value: "key", label: t("studio.filterKey") },
          ]}
        />
      </div>
      {rows.length === 0 ? <EmptyState>{t("studio.noEvents")}</EmptyState> : null}
      <ol
        className="flex-1 overflow-auto text-sm"
        data-testid="event-feed"
        tabIndex={0}
        aria-label={t("studio.events")}
      >
        {rows.map((event) => {
          const type = eventType(event);
          const corner = type === "pass" && "play_type" in event && event.play_type === "corner";
          const label = t((corner ? "event.corner" : `event.${type}`) as Key);
          const detail = eventDetail(event);
          const team = teamCard(scenario, event.team);
          const key = isKeyEvent(event);
          return (
            <li
              key={event.id}
              data-testid="event-row"
              data-id={event.id}
              data-type={type}
              className={`flex items-center gap-2 border-b border-line/60 px-2 py-1 ${key ? "bg-panel-2 font-medium" : ""}`}
            >
              <span className="tabular w-11 shrink-0 text-right text-xs text-muted">
                {formatMinute(event.period, event.clock_ms)}
              </span>
              <span aria-hidden className="w-4 text-center">
                {EVENT_ICONS[type] ?? "·"}
              </span>
              {team ? <TeamChip small name={team.short_name} color={team.primary_color} /> : null}
              <span className="truncate">
                {label}
                {event.player ? ` · ${playerName(players, event.player)}` : ""}
                {detail && type !== "pass" ? ` · ${detailText(detail)}` : ""}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
