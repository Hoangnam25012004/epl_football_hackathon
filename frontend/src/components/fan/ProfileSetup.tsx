"use client";

// How a fan wants to follow the match (docs/day4-design.md section 7.1). The two register cards
// show a real line of this match, rendered by the server for each register (never a sentence
// written by the UI); the metric list and its labels come from the server's stats panel.
import { useEffect, useState } from "react";
import { Segmented, TeamChip } from "@/components/common/ui";
import { api } from "@/lib/api";
import type { PlayerBrief, ScenarioInfo, StatsRow } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import { profileQuery, type Lang, type Mode, type ViewerProfile } from "@/lib/profile";

function useExample(matchId: string, mode: Mode, language: Lang): string | null {
  const [example, setExample] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const query = profileQuery({
      mode,
      language,
      team: null,
      player: null,
      beginner: false,
      metric: null,
    });
    void api.commentary(matchId, query).then((result) => {
      if (!alive || !result.ok) return;
      const items = result.data.filter((item) => item.importance >= 2);
      setExample((items[items.length - 1] ?? result.data[result.data.length - 1])?.text ?? null);
    });
    return () => {
      alive = false;
    };
  }, [matchId, mode, language]);
  return example;
}

export function ProfileSetup({
  matchId,
  initial,
  scenario,
  players,
  onDone,
}: {
  matchId: string;
  initial: ViewerProfile;
  scenario: ScenarioInfo | null;
  players: Map<string, PlayerBrief>;
  onDone: (profile: ViewerProfile) => void;
}) {
  const { t } = useT();
  const [profile, setProfile] = useState<ViewerProfile>(initial);
  const [metrics, setMetrics] = useState<StatsRow[]>([]);
  const analystExample = useExample(matchId, "analyst", profile.language);
  const casualExample = useExample(matchId, "casual", profile.language);
  const set = <K extends keyof ViewerProfile>(key: K, value: ViewerProfile[K]) =>
    setProfile((current) => ({ ...current, [key]: value }));

  useEffect(() => {
    let alive = true;
    void api.stats(matchId, "FULL").then((result) => {
      if (alive && result.ok) setMetrics(result.data.rows);
    });
    return () => {
      alive = false;
    };
  }, [matchId]);

  const squads = scenario
    ? [scenario.home, scenario.away].map((team) => ({
        team,
        players: [...players.values()].filter((p) => p.team === team.id),
      }))
    : [];

  return (
    <form
      data-testid="profile-setup"
      className="mx-auto flex max-w-2xl flex-col gap-5 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        onDone(profile);
      }}
    >
      <h1 className="text-xl font-extrabold">{t("fan.setupTitle")}</h1>
      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label={t("fan.setupTitle")}>
        {(["casual", "analyst"] as const).map((mode) => {
          const active = profile.mode === mode;
          const example = mode === "analyst" ? analystExample : casualExample;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={active}
              data-testid={`mode-${mode}`}
              onClick={() => set("mode", mode)}
              className={`flex flex-col gap-1 rounded-lg border-2 p-3 text-left ${
                active ? "border-accent bg-accent/10" : "border-line bg-panel"
              }`}
            >
              <span className="font-bold">
                {t(mode === "analyst" ? "fan.analyst" : "fan.casual")}
              </span>
              <span className="text-sm text-muted">
                {t(mode === "analyst" ? "fan.analystDesc" : "fan.casualDesc")}
              </span>
              {example ? (
                <span className="mt-1 border-l-2 border-line pl-2 text-sm italic">
                  {t("fan.example")}: “{example}”
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">{t("fan.language")}</legend>
        <Segmented<Lang>
          label={t("fan.language")}
          value={profile.language}
          onChange={(value) => set("language", value)}
          options={[
            { value: "vi", label: t("profile.vi") },
            { value: "en", label: t("profile.en") },
          ]}
        />
      </fieldset>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">{t("fan.club")}</legend>
        <div className="flex flex-wrap gap-2">
          <ChoiceButton
            active={profile.team === null}
            onClick={() => set("team", null)}
            testId="club-none"
          >
            {t("fan.neutral")}
          </ChoiceButton>
          {scenario
            ? [scenario.home, scenario.away].map((team) => (
                <ChoiceButton
                  key={team.id}
                  active={profile.team === team.id}
                  onClick={() => set("team", team.id)}
                  testId={`club-${team.id}`}
                >
                  <TeamChip small name={team.short_name} color={team.primary_color} /> {team.name}
                </ChoiceButton>
              ))
            : null}
        </div>
      </fieldset>
      <label className="flex items-start gap-3 rounded-lg border border-line bg-panel p-3">
        <input
          type="checkbox"
          data-testid="beginner-setup"
          checked={profile.beginner}
          onChange={(event) => set("beginner", event.target.checked)}
          className="mt-1 h-4 w-4"
        />
        <span>
          <span className="block font-semibold">{t("fan.beginner")}</span>
          <span className="text-sm text-muted">{t("fan.beginnerDesc")}</span>
        </span>
      </label>
      <details className="rounded-lg border border-line bg-panel p-3">
        <summary className="cursor-pointer text-sm font-semibold">{t("fan.advanced")}</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            {t("fan.player")}
            <select
              data-testid="player-select"
              value={profile.player ?? ""}
              onChange={(event) => set("player", event.target.value || null)}
              className="rounded border border-line bg-bg px-2 py-1"
            >
              <option value="">{t("common.none")}</option>
              {squads.map(({ team, players: squad }) => (
                <optgroup key={team.id} label={team.name}>
                  {squad.map((player) => (
                    <option key={player.id} value={player.id}>
                      {player.shirt}. {player.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("fan.metric")}
            <select
              data-testid="metric-select"
              value={profile.metric ?? ""}
              onChange={(event) => set("metric", event.target.value || null)}
              className="rounded border border-line bg-bg px-2 py-1"
            >
              <option value="">{t("common.none")}</option>
              {metrics.map((row) => (
                <option key={row.metric} value={row.metric}>
                  {profile.language === "vi" ? row.label_vi : row.label_en}
                </option>
              ))}
            </select>
          </label>
        </div>
      </details>
      <button
        type="submit"
        data-testid="profile-start"
        className="rounded-lg bg-accent px-4 py-3 text-base font-bold text-white"
      >
        {t("fan.start")}
      </button>
    </form>
  );
}

function ChoiceButton({
  active,
  onClick,
  children,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      data-testid={testId}
      onClick={onClick}
      className={`flex items-center gap-2 rounded-lg border-2 px-3 py-2 text-sm ${
        active ? "border-accent bg-accent/10" : "border-line bg-panel"
      }`}
    >
      {children}
    </button>
  );
}
