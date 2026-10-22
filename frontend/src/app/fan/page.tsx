"use client";

// /fan/?match=ID: the fan player, Vietnamese first (docs/day4-design.md section 7). The profile is
// the WebSocket query: changing it (even the "new to football" switch) opens a new connection,
// whose hello re-renders the latest commentary for the new profile.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useStore } from "zustand";
import { ConnectionBanner } from "@/components/common/ConnectionBanner";
import { PageShell } from "@/components/common/PageShell";
import { Scoreboard } from "@/components/common/Scoreboard";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { useToast } from "@/components/common/Toast";
import { EmptyState, LangToggle, Tabs } from "@/components/common/ui";
import { CommentaryFeed, PlayerTab, RecapPanel } from "@/components/fan/FanFeed";
import { ProfileSetup } from "@/components/fan/ProfileSetup";
import { api, parseMatchId } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  DEFAULT_PROFILE,
  clearProfile,
  fitProfile,
  loadProfile,
  profileQuery,
  saveProfile,
  type ViewerProfile,
} from "@/lib/profile";
import { items } from "@/store/match-store";
import { useMatchConnection } from "@/hooks/useMatchConnection";
import { usePlayers } from "@/hooks/usePlayers";
import { scenarioOf, useScenarios } from "@/hooks/useScenarios";

export default function FanPage() {
  return (
    <PageShell defaultLang="vi">
      <Fan />
    </PageShell>
  );
}

type Tab = "commentary" | "player" | "recap";

function Fan() {
  const { t } = useT();
  const toast = useToast();
  const params = useSearchParams();
  const matchId = params.get("match");
  const scenarios = useScenarios();
  const scenario = scenarioOf(scenarios, matchId);
  const players = usePlayers(matchId);
  const [stored] = useState<ViewerProfile | null>(() => loadProfile());
  const [profile, setProfile] = useState<ViewerProfile | null>(stored);
  const [editing, setEditing] = useState(stored === null);
  const [tab, setTab] = useState<Tab>("commentary");

  // a club or player of another match is dropped before connecting (a 4422 loop otherwise)
  const teams = useMemo(() => (scenario ? [scenario.home.id, scenario.away.id] : null), [scenario]);
  const fitted = useMemo(() => {
    if (!profile || !teams) return null;
    return fitProfile(profile, teams, players.size ? [...players.keys()] : null);
  }, [profile, teams, players]);
  const live = !editing && fitted !== null;
  const connection = useMatchConnection(live ? matchId : null, fitted ? profileQuery(fitted) : "", {
    name: "fan",
  });
  const { store, socketState } = connection;
  const match = useStore(store, (s) => s.match);
  const snapshot = useStore(store, (s) => s.snapshot);
  const status = useStore(store, (s) => s.status);
  const events = useStore(store, (s) => s.events);
  const commentary = useStore(store, (s) => s.commentary);
  const recaps = useStore(store, (s) => s.recaps);

  // a profile the server refuses (4422) is chosen again; a refused socket never retries
  const refused = socketState.status === "fatal" && socketState.code === 4422;

  if (!matchId) {
    return (
      <main className="p-6">
        <EmptyState>
          {t("common.noMatch")}{" "}
          <Link className="text-accent underline" href="/">
            {t("common.back")}
          </Link>
        </EmptyState>
      </main>
    );
  }

  const choose = (next: ViewerProfile) => {
    if (refused) clearProfile();
    saveProfile(next);
    setProfile(next);
    setEditing(false);
  };

  const toggleBeginner = () => {
    if (!profile) return;
    const next = { ...profile, beginner: !profile.beginner };
    saveProfile(next);
    setProfile(next);
    toast.show(next.beginner ? t("fan.beginnerOn") : t("fan.beginnerOff"));
  };

  const header = (
    <header className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-2">
      <Link href="/" className="font-extrabold">
        {t("app.name")}
      </Link>
      <span className="ml-auto flex items-center gap-2">
        <LangToggle />
        <SyntheticBadge compact />
      </span>
    </header>
  );

  if (editing || refused || !profile) {
    return (
      <div className="min-h-dvh">
        {header}
        <ProfileSetup
          matchId={matchId}
          initial={profile ?? DEFAULT_PROFILE}
          scenario={scenario}
          players={players}
          onDone={choose}
        />
      </div>
    );
  }

  const focusName = profile.player ? (players.get(profile.player)?.name ?? null) : null;
  return (
    <div className="flex h-dvh flex-col">
      {header}
      <ConnectionBanner
        state={socketState}
        onCreate={async () => {
          const parsed = parseMatchId(matchId);
          if (!parsed) return;
          const result = await api.createMatch({ ...parsed, autostart: true });
          if (result.ok) window.location.reload();
        }}
      />
      <section className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-3 py-3">
        <Scoreboard
          match={match}
          snapshot={snapshot}
          latest={events[events.length - 1]}
          status={status}
          scenario={scenario}
          large
        />
        <div className="flex items-center gap-2">
          <label className="flex cursor-pointer items-center gap-2 rounded-full border border-line px-3 py-1.5 text-sm">
            <input
              type="checkbox"
              data-testid="beginner-toggle"
              checked={profile.beginner}
              onChange={toggleBeginner}
              className="h-4 w-4"
            />
            {t("fan.beginner")}
          </label>
          <button
            type="button"
            data-testid="change-profile"
            onClick={() => setEditing(true)}
            className="rounded-full border border-line px-3 py-1.5 text-sm"
          >
            {t("fan.change")}
          </button>
        </div>
      </section>
      <Tabs<Tab>
        label={t("fan.title")}
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "commentary", label: t("fan.tabCommentary"), testId: "tab-commentary" },
          ...(profile.player
            ? [{ value: "player" as const, label: t("fan.tabPlayer"), testId: "tab-player" }]
            : []),
          { value: "recap", label: t("fan.tabRecap"), testId: "tab-recap" },
        ]}
      />
      <main className="min-h-0 flex-1 overflow-hidden">
        {tab === "commentary" ? (
          <CommentaryFeed
            items={items(commentary)}
            scenario={scenario}
            ownTeam={fitted?.team ?? null}
            waiting={status === "created"}
          />
        ) : null}
        {tab === "player" ? <PlayerTab items={items(commentary)} playerName={focusName} /> : null}
        {tab === "recap" ? <RecapPanel recaps={recaps} /> : null}
      </main>
    </div>
  );
}
