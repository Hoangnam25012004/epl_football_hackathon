"use client";

// /studio/?match=ID: the commentator and editor dashboard (docs/day4-design.md section 5).
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";
import { useStore } from "zustand";
import { ConnectionBanner } from "@/components/common/ConnectionBanner";
import { PageShell } from "@/components/common/PageShell";
import { Scoreboard } from "@/components/common/Scoreboard";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { useToast } from "@/components/common/Toast";
import { EmptyState, LangToggle, Panel } from "@/components/common/ui";
import { ClaimBox } from "@/components/studio/ClaimBox";
import { CommentaryPreview } from "@/components/studio/CommentaryPreview";
import { EventFeed } from "@/components/studio/EventFeed";
import { ItemDrawer, type DrawerTarget } from "@/components/studio/ItemDrawer";
import { LowerThirdMonitor } from "@/components/studio/LowerThirdMonitor";
import { MomentumTimeline } from "@/components/studio/MomentumTimeline";
import { ReplayControls } from "@/components/studio/ReplayControls";
import { StatsPanel } from "@/components/studio/StatsPanel";
import { TalkingPointQueue } from "@/components/studio/TalkingPointQueue";
import { api, parseMatchId } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { items } from "@/store/match-store";
import { useMatchConnection } from "@/hooks/useMatchConnection";
import { usePlayers } from "@/hooks/usePlayers";
import { scenarioOf, useScenarios } from "@/hooks/useScenarios";

export default function StudioPage() {
  return (
    <PageShell defaultLang="en" theme="studio">
      <Studio />
    </PageShell>
  );
}

function Studio() {
  const { t, lang } = useT();
  const toast = useToast();
  const params = useSearchParams();
  const matchId = params.get("match");
  const connection = useMatchConnection(matchId, `mode=analyst&lang=${lang}`, { name: "studio" });
  const { store } = connection;
  const match = useStore(store, (s) => s.match);
  const snapshot = useStore(store, (s) => s.snapshot);
  const status = useStore(store, (s) => s.status);
  const speed = useStore(store, (s) => s.speed);
  const events = useStore(store, (s) => s.events);
  const claims = useStore(store, (s) => s.claims);
  const talkingPoints = useStore(store, (s) => s.talkingPoints);
  const pending = useStore(store, (s) => s.pending.talkingPoints);
  const commentary = useStore(store, (s) => s.commentary);
  const helloCount = useStore(store, (s) => s.helloCount);
  const momentsCount = useStore(store, (s) => s.moments.length);
  const scenarios = useScenarios();
  const scenario = scenarioOf(scenarios, matchId);
  const players = usePlayers(matchId);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);

  const createThisMatch = useCallback(async () => {
    const parsed = matchId ? parseMatchId(matchId) : null;
    if (!parsed) return;
    const result = await api.createMatch({ ...parsed, autostart: false });
    if (result.ok) window.location.reload();
    else toast.show(result.error.message, "error");
  }, [matchId, toast]);

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

  const snapshotVersion = (snapshot?.as_of_seq ?? 0) + momentsCount;
  const examples = scenario?.example_claims[lang] ?? [];
  return (
    <div className="flex h-dvh flex-col">
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
        <ReplayControls matchId={matchId} status={status} speed={speed} />
        <nav className="ml-auto flex items-center gap-3 text-xs">
          <a
            className="underline"
            href={`/overlay/?match=${matchId}&lang=${lang}`}
            target="_blank"
            rel="noreferrer"
          >
            Overlay ↗
          </a>
          <a className="underline" href={`/fan/?match=${matchId}`} target="_blank" rel="noreferrer">
            Fan ↗
          </a>
          <a
            className="underline"
            href={`/compare/?match=${matchId}`}
            target="_blank"
            rel="noreferrer"
          >
            {t("landing.compare")} ↗
          </a>
          <LangToggle />
          <SyntheticBadge />
        </nav>
      </header>
      <ConnectionBanner state={connection.socketState} onCreate={() => void createThisMatch()} />
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-auto p-2 xl:grid-cols-[minmax(260px,1fr)_minmax(420px,1.8fr)_minmax(320px,1.2fr)] xl:overflow-hidden">
        <Panel title={t("studio.events")} testId="panel-events" className="min-h-80">
          <EventFeed
            matchId={matchId}
            live={events}
            helloCount={helloCount}
            scenario={scenario}
            players={players}
          />
        </Panel>
        <div className="flex min-h-0 flex-col gap-2">
          <Panel title={t("studio.claim")} testId="panel-claims" className="max-h-[45%] min-h-56">
            <ClaimBox
              matchId={matchId}
              examples={examples}
              history={items(claims)}
              onDetails={(id) => setDrawer({ kind: "claim", id })}
            />
          </Panel>
          <Panel
            title={t("studio.talkingPoints")}
            testId="panel-talking-points"
            className="min-h-64 flex-1"
          >
            <TalkingPointQueue
              matchId={matchId}
              points={items(talkingPoints)}
              pending={pending}
              store={store}
              onTrace={(id) => setDrawer({ kind: "studio", id })}
            />
          </Panel>
          <Panel
            title={t("studio.commentary")}
            testId="panel-commentary"
            className="max-h-[30%] min-h-40"
          >
            <CommentaryPreview
              items={items(commentary)}
              onDetails={(id) => setDrawer({ kind: "studio", id })}
            />
          </Panel>
        </div>
        <div className="flex min-h-0 flex-col gap-2 overflow-auto">
          <Panel title={t("studio.lowerThirds")} testId="panel-lower-thirds">
            <LowerThirdMonitor matchId={matchId} main={connection} uiLang={lang} />
          </Panel>
          <Panel title={t("studio.momentum")} testId="panel-momentum">
            <MomentumTimeline matchId={matchId} version={snapshotVersion} scenario={scenario} />
          </Panel>
          <Panel title={t("studio.stats")} testId="panel-stats">
            <StatsPanel matchId={matchId} version={snapshotVersion} scenario={scenario} />
          </Panel>
        </div>
      </main>
      <ItemDrawer
        matchId={matchId}
        target={drawer}
        onClose={() => setDrawer(null)}
        homeId={match?.home.id ?? ""}
        scenario={scenario}
        players={players}
      />
    </div>
  );
}
