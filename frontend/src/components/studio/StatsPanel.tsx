"use client";

// Match stats from the Stats Tools (docs/day4-design.md sections 3.4, 5.8): the bars use the
// server's values, the words its displays. Refreshed with the snapshots, at most every 2 s.
import { useEffect, useState } from "react";
import { EmptyState, Segmented } from "@/components/common/ui";
import { api } from "@/lib/api";
import { safeColor } from "@/lib/color";
import type { ScenarioInfo, StatsPanel as Panel } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import { teamCard } from "@/hooks/useScenarios";
import { useThrottled } from "@/hooks/useThrottled";

type Period = Panel["period"];
const REFRESH_MS = 2_000;

export function StatsPanel({
  matchId,
  version,
  scenario,
}: {
  matchId: string;
  version: number;
  scenario: ScenarioInfo | null;
}) {
  const { t, lang } = useT();
  const [period, setPeriod] = useState<Period>("FULL");
  const [panel, setPanel] = useState<Panel | null>(null);

  const refresh = useThrottled(version, REFRESH_MS);
  useEffect(() => {
    let alive = true;
    void api.stats(matchId, period).then((result) => {
      if (alive && result.ok) setPanel(result.data);
    });
    return () => {
      alive = false;
    };
  }, [matchId, period, refresh]);

  const home = teamCard(scenario, panel?.home);
  const away = teamCard(scenario, panel?.away);
  return (
    <div className="flex flex-col gap-2 p-2" data-testid="stats-panel">
      <Segmented<Period>
        label={t("studio.stats")}
        value={period}
        onChange={setPeriod}
        options={[
          { value: "FULL", label: "FULL" },
          { value: "H1", label: "H1" },
          { value: "H2", label: "H2" },
        ]}
      />
      {!panel ? <EmptyState>{t("common.loading")}</EmptyState> : null}
      <table className="w-full text-xs">
        <tbody>
          {panel?.rows.map((row) => {
            const a = row.home.value ?? 0;
            const b = row.away.value ?? 0;
            const share = a + b > 0 ? a / (a + b) : 0.5;
            return (
              <tr key={row.metric} data-testid="stats-row" data-metric={row.metric}>
                <td className="tabular w-12 py-1 text-right font-semibold">{row.home.display}</td>
                <td className="px-2 py-1">
                  <div className="text-center text-muted">
                    {lang === "vi" ? row.label_vi : row.label_en}
                  </div>
                  <div className="mt-0.5 flex h-1.5 overflow-hidden rounded bg-panel-2">
                    <span
                      style={{
                        width: `${share * 100}%`,
                        backgroundColor: safeColor(home?.primary_color),
                      }}
                    />
                    <span
                      style={{
                        width: `${(1 - share) * 100}%`,
                        backgroundColor: safeColor(away?.primary_color),
                      }}
                    />
                  </div>
                </td>
                <td className="tabular w-12 py-1 font-semibold">{row.away.display}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
