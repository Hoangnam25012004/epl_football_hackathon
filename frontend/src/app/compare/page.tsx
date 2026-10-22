"use client";

// /compare/?match=ID&left=analyst.en&right=casual.vi.beginner: two real viewer connections with
// two profiles, the same moments side by side (docs/day4-design.md section 8).
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { useStore } from "zustand";
import { ConnectionBanner } from "@/components/common/ConnectionBanner";
import { PageShell } from "@/components/common/PageShell";
import { Scoreboard } from "@/components/common/Scoreboard";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { NoMatch } from "@/components/common/NoMatch";
import { Chip, EmptyState, LangToggle, Segmented } from "@/components/common/ui";
import { CommentaryCard } from "@/components/fan/CommentaryCard";
import { alignByItem, formatSide, observed, parseSide } from "@/lib/compare";
import type { CommentaryView, ScenarioInfo } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import { DEFAULT_PROFILE, profileQuery, type ViewerProfile } from "@/lib/profile";
import { items } from "@/store/match-store";
import { useMatchConnection } from "@/hooks/useMatchConnection";
import { scenarioOf, useScenarios } from "@/hooks/useScenarios";

const LEFT: ViewerProfile = { ...DEFAULT_PROFILE, mode: "analyst", language: "en" };
const RIGHT: ViewerProfile = { ...DEFAULT_PROFILE, mode: "casual", language: "vi", beginner: true };
const SHOWN = 40;

export default function ComparePage() {
  return (
    <PageShell defaultLang="en">
      <Compare />
    </PageShell>
  );
}

function Compare() {
  const { t } = useT();
  const params = useSearchParams();
  const matchId = params.get("match");
  const scenarios = useScenarios();
  const scenario = scenarioOf(scenarios, matchId);
  const [left, setLeft] = useState(() => parseSide(params.get("left"), LEFT));
  const [right, setRight] = useState(() => parseSide(params.get("right"), RIGHT));
  const a = useMatchConnection(matchId, profileQuery(left), { name: "compare-left" });
  const b = useMatchConnection(matchId, profileQuery(right), { name: "compare-right" });
  const match = useStore(a.store, (s) => s.match);
  const snapshot = useStore(a.store, (s) => s.snapshot);
  const status = useStore(a.store, (s) => s.status);
  const events = useStore(a.store, (s) => s.events);
  const leftItems = useStore(a.store, (s) => s.commentary);
  const rightItems = useStore(b.store, (s) => s.commentary);

  if (!matchId) return <NoMatch />;

  const rows = alignByItem(items(leftItems), items(rightItems)).slice(0, SHOWN);
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-3 py-2">
        <Link href="/" className="font-extrabold">
          {t("app.name")}
        </Link>
        <Scoreboard
          match={match}
          snapshot={snapshot}
          latest={events[events.length - 1]}
          status={status}
          scenario={scenario}
        />
        <span className="ml-auto flex items-center gap-2">
          <LangToggle />
          <SyntheticBadge />
        </span>
      </header>
      <ConnectionBanner state={a.socketState.status !== "open" ? a.socketState : b.socketState} />
      <section className="px-3 py-3">
        <h1 className="text-xl font-extrabold">{t("compare.title")}</h1>
        <p className="text-sm text-muted">{t("compare.subtitle")}</p>
      </section>
      <div className="grid grid-cols-2 gap-3 px-3">
        <SidePicker
          label={t("compare.left")}
          profile={left}
          onChange={setLeft}
          testId="compare-left"
        />
        <SidePicker
          label={t("compare.right")}
          profile={right}
          onChange={setRight}
          testId="compare-right"
        />
      </div>
      <main className="flex flex-col gap-3 p-3" data-testid="compare-rows">
        {rows.length === 0 ? <EmptyState>{t("compare.empty")}</EmptyState> : null}
        {rows.map((row, index) => (
          <div
            key={row.id}
            data-testid="compare-row"
            data-id={row.id}
            className={`grid grid-cols-2 gap-3 ${index === 0 ? "rounded-xl ring-2 ring-accent/60 ring-offset-2 ring-offset-bg" : ""}`}
          >
            <Side item={row.left} scenario={scenario} profile={left} />
            <Side item={row.right} scenario={scenario} profile={right} />
          </div>
        ))}
      </main>
    </div>
  );
}

function Side({
  item,
  scenario,
  profile,
}: {
  item: CommentaryView | undefined;
  scenario: ScenarioInfo | null;
  profile: ViewerProfile;
}) {
  const { t } = useT();
  if (!item) return <div className="rounded-lg border border-dashed border-line" />;
  const seen = observed(item);
  return (
    <div className="flex flex-col gap-1">
      <CommentaryCard item={item} scenario={scenario} ownTeam={profile.team} />
      <div className="flex flex-wrap gap-1">
        <Chip>{t("compare.numbers", { n: seen.numbers })}</Chip>
        <Chip>{t("compare.words", { n: seen.words })}</Chip>
        {seen.rule ? <Chip tone="accent">{t("compare.rule")}</Chip> : null}
      </div>
    </div>
  );
}

function SidePicker({
  label,
  profile,
  onChange,
  testId,
}: {
  label: string;
  profile: ViewerProfile;
  onChange: (profile: ViewerProfile) => void;
  testId: string;
}) {
  const { t } = useT();
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel p-2"
      data-testid={testId}
      data-profile={formatSide(profile)}
    >
      <span className="text-xs font-bold uppercase text-muted">{label}</span>
      <Segmented<ViewerProfile["mode"]>
        label={`${label} mode`}
        value={profile.mode}
        onChange={(mode) => onChange({ ...profile, mode })}
        options={[
          { value: "analyst", label: t("profile.analyst") },
          { value: "casual", label: t("profile.casual") },
        ]}
      />
      <Segmented<ViewerProfile["language"]>
        label={`${label} language`}
        value={profile.language}
        onChange={(language) => onChange({ ...profile, language })}
        options={[
          { value: "en", label: "EN" },
          { value: "vi", label: "VI" },
        ]}
      />
      <label className="flex items-center gap-1 text-xs">
        <input
          type="checkbox"
          checked={profile.beginner}
          onChange={(event) => onChange({ ...profile, beginner: event.target.checked })}
        />
        {t("profile.beginner")}
      </label>
    </div>
  );
}
