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
import { PrivateChip } from "@/components/common/PrivateChip";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { useToast } from "@/components/common/Toast";
import { NoMatch } from "@/components/common/NoMatch";
import { LangToggle, Tabs } from "@/components/common/ui";
import { CommentaryFeed, PlayerTab, RecapPanel } from "@/components/fan/FanFeed";
import { ProfileSetup } from "@/components/fan/ProfileSetup";
import { VoiceControl } from "@/components/fan/VoiceControl";
import { api, parseMatchId, sessionBody } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  DEFAULT_PROFILE,
  clearProfile,
  fitProfile,
  loadProfile,
  loadVoiceOn,
  profileQuery,
  saveProfile,
  saveVoiceOn,
  type ViewerProfile,
  type VoiceGender,
} from "@/lib/profile";
import { items } from "@/store/match-store";
import { useCommentaryAudio } from "@/hooks/useCommentaryAudio";
import { useMatchConnection } from "@/hooks/useMatchConnection";
import { usePlayers } from "@/hooks/usePlayers";
import { scenarioOf, useScenarios } from "@/hooks/useScenarios";
import { useSpeechHealth } from "@/hooks/useSpeechHealth";

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
  // Day 5: the voice. Off on every page load (browsers need a tap before sound); the voice
  // chosen is kept in the profile, and whether it was on, for a "tap to resume" button.
  const { controller, audio } = useCommentaryAudio();
  const provider = useSpeechHealth().tts;
  const [voiceOn, setVoiceOn] = useState(false);
  const [resume, setResume] = useState(() => loadVoiceOn());
  const gender: VoiceGender = profile?.voice ?? "female";

  // a club or player of another match is dropped before connecting (a 4422 loop otherwise)
  const teams = useMemo(() => (scenario ? [scenario.home.id, scenario.away.id] : null), [scenario]);
  const fitted = useMemo(() => {
    if (!profile || !teams) return null;
    return fitProfile(profile, teams, players.size ? [...players.keys()] : null);
  }, [profile, teams, players]);
  const live = !editing && fitted !== null;
  // the server reads aloud only for a connection that asks (`voice` in the query)
  const query = fitted ? profileQuery({ ...fitted, voice: voiceOn ? gender : null }) : "";
  const connection = useMatchConnection(live ? matchId : null, query, {
    name: "fan",
    onLive: controller.onLive,
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

  if (!matchId) return <NoMatch />;

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

  const greet = (voice: VoiceGender) => {
    void api.greeting("vi", voice).then((result) => {
      if (result.ok) controller.greet(result.data);
    });
  };

  // runs inside the click: `enable` unlocks audio before anything awaits
  const toggleVoice = () => {
    if (audio.needsGesture) {
      controller.enable();
      return;
    }
    if (voiceOn) {
      controller.disable();
      setVoiceOn(false);
      saveVoiceOn(false);
      toast.show(t("fan.voiceOffToast"));
      return;
    }
    controller.enable();
    setVoiceOn(true);
    setResume(false);
    saveVoiceOn(true);
    if (profile && !profile.voice) {
      const next = { ...profile, voice: gender };
      saveProfile(next);
      setProfile(next);
    }
    toast.show(t("fan.voiceOnToast"));
    greet(gender);
  };

  const chooseVoice = (voice: VoiceGender) => {
    if (!profile || voice === profile.voice) return;
    const next = { ...profile, voice };
    saveProfile(next);
    setProfile(next); // a new query: the next lines come in this voice
    greet(voice);
  };

  const header = (
    <header className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-2">
      <Link href="/" className="font-extrabold">
        {t("app.name")}
      </Link>
      <span className="ml-auto flex items-center gap-2">
        <LangToggle />
        <PrivateChip matchId={matchId} />
        <SyntheticBadge compact />
      </span>
    </header>
  );

  if (editing || refused || !profile) {
    return (
      <div className="min-h-dvh">
        {header}
        {refused ? <ConnectionBanner state={socketState} /> : null}
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
          const result = await api.createMatch({ ...sessionBody(parsed), autostart: true });
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
        <div className="flex flex-wrap items-center gap-2">
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
        <VoiceControl
          on={voiceOn}
          resume={resume}
          gender={gender}
          language={profile.language}
          provider={provider}
          audio={audio}
          onToggle={toggleVoice}
          onGender={chooseVoice}
          onSkip={() => controller.dispatch({ type: "skip" })}
        />
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
            speakingId={audio.speakingId}
            voiceOn={audio.on}
          />
        ) : null}
        {tab === "player" ? <PlayerTab items={items(commentary)} playerName={focusName} /> : null}
        {tab === "recap" ? (
          <RecapPanel
            recaps={recaps}
            audio={
              provider === "off"
                ? null
                : {
                    matchId,
                    language: profile.language,
                    mode: profile.mode,
                    gender,
                    controller,
                  }
            }
          />
        ) : null}
      </main>
    </div>
  );
}
