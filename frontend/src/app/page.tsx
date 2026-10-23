"use client";

// "/": pick a scenario and a seed, open the studio, the fan player, the overlay or the comparison
// (docs/day4-design.md section 9). Day 6 (docs/day6-design.md section 6.5): start a private demo
// match of one's own, find "my matches" again.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageShell } from "@/components/common/PageShell";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { useToast } from "@/components/common/Toast";
import { Chip, LangToggle, TeamChip } from "@/components/common/ui";
import { api, type ApiError } from "@/lib/api";
import type { CreateMatch, Health, MatchInfo, ScenarioInfo } from "@/lib/contracts";
import { hasKey, useT } from "@/lib/i18n";
import { forgetSession, mySessions, rememberSession } from "@/lib/sessions";
import { useScenarios } from "@/hooks/useScenarios";

type CreateBody = Partial<CreateMatch> & { scenario: CreateMatch["scenario"] };

export default function LandingPage() {
  return (
    <PageShell defaultLang="en">
      <Landing />
    </PageShell>
  );
}

/** Create a match; a refusal is said in words (and when to try again). */
function useCreate(): (body: CreateBody) => Promise<MatchInfo | null> {
  const { t } = useT();
  const toast = useToast();
  return useCallback(
    async (body: CreateBody) => {
      const result = await api.createMatch(body);
      if (result.ok) {
        if (result.data.private) rememberSession(result.data.match_id);
        return result.data;
      }
      toast.show(refusal(result.error, t), "error");
      return null;
    },
    [t, toast],
  );
}

function refusal(error: ApiError, t: ReturnType<typeof useT>["t"]): string {
  if (error.code === "busy" && error.retryAfterS !== undefined) {
    const s = Math.ceil(error.retryAfterS);
    return error.status === 429 ? t("landing.busyMany", { s }) : t("landing.busyFull", { s });
  }
  return t("landing.createFailed", { message: error.message });
}

function Landing() {
  const { t, lang } = useT();
  const router = useRouter();
  const create = useCreate();
  const scenarios = useScenarios();
  const [sessions, setSessions] = useState<MatchInfo[] | null>(null);
  const [mine, setMine] = useState<MatchInfo[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      const [matches, server] = await Promise.all([api.matches(), api.health()]);
      // my private sessions: still there, or forgotten once the server removed them
      const remembered = await Promise.all(mySessions().map((s) => api.match(s.id)));
      if (!alive) return;
      if (matches.ok) setSessions(matches.data);
      if (server.ok) setHealth(server.data);
      const kept: MatchInfo[] = [];
      remembered.forEach((result, index) => {
        if (result.ok) kept.push(result.data.info);
        else if (result.error.code === "not_found") forgetSession(mySessions()[index]?.id ?? "");
      });
      setMine(kept);
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const startDemo = async () => {
    setStarting(true);
    const info = await create({
      scenario: "dominant_loser",
      seed: 42,
      speed: 20,
      private: true,
      autostart: false,
    });
    setStarting(false);
    if (info) router.push(`/studio/?match=${info.match_id}`);
  };

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold">{t("app.name")}</h1>
          <p className="text-sm text-muted">{t("app.tagline")}</p>
        </div>
        <div className="flex items-center gap-2">
          <SyntheticBadge />
          <LangToggle />
        </div>
      </header>
      <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
        {t("common.syntheticNote")}
      </p>
      <section
        data-testid="demo-start"
        className="flex flex-col items-start gap-2 rounded-lg border border-accent/40 bg-panel p-4"
      >
        <button
          type="button"
          data-testid="start-my-demo"
          disabled={starting}
          onClick={() => void startDemo()}
          className="rounded bg-accent-fill px-4 py-2 font-bold text-white disabled:opacity-50"
        >
          ▶ {t("landing.startDemo")}
        </button>
        <p className="text-sm text-muted">{t("landing.startDemoHint")}</p>
      </section>
      {health ? (
        <div
          data-testid="server-health"
          className="flex flex-wrap items-center gap-2 text-xs text-muted"
        >
          <span>{t("landing.server")}:</span>
          <Chip tone="accent">
            {t("landing.llm", { provider: health.llm_provider, model: health.model })}
          </Chip>
          {health.degraded.map((flag) => {
            // in words ("LLM paused (circuit open)"); an unknown flag as the server wrote it
            const key = `degraded.${flag}`;
            return (
              <Chip key={flag} tone="warn">
                {hasKey(key) ? t(key) : flag}
              </Chip>
            );
          })}
        </div>
      ) : null}
      {mine.length ? (
        <section className="flex flex-col gap-2" data-testid="my-sessions">
          <h2 className="text-lg font-bold">{t("landing.mySessions")}</h2>
          <SessionList sessions={mine} lang={lang} />
        </section>
      ) : null}
      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-bold">{t("landing.title")}</h2>
        <p className="text-sm text-muted">{t("landing.subtitle")}</p>
        <div className="grid gap-3 md:grid-cols-2">
          {(scenarios ?? []).map((scenario) => (
            <ScenarioCard key={scenario.key} scenario={scenario} lang={lang} create={create} />
          ))}
        </div>
        {scenarios === null ? <p className="text-sm text-muted">{t("common.loading")}</p> : null}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-lg font-bold">{t("landing.sessions")}</h2>
        {sessions && sessions.length === 0 ? (
          <p className="text-sm text-muted">{t("landing.noSessions")}</p>
        ) : null}
        <SessionList sessions={sessions ?? []} lang={lang} />
      </section>
    </main>
  );
}

function SessionList({ sessions, lang }: { sessions: MatchInfo[]; lang: "en" | "vi" }) {
  const { t } = useT();
  return (
    <ul className="flex flex-col gap-1">
      {sessions.map((session) => (
        <li
          key={session.match_id}
          data-testid="session-row"
          className="flex flex-wrap items-center gap-2 rounded border border-line bg-panel px-3 py-2 text-sm"
        >
          <span className="font-mono break-all">{session.match_id}</span>
          <Chip>{t(`status.${session.status}`)}</Chip>
          <span className="text-muted">
            {session.events_applied}/{session.events_total}
          </span>
          {/* no prefetch: the static export writes the pages' segment files where the
              client does not look for them (404 in the console); the pages are small */}
          <span className="ml-auto flex gap-2">
            <Link
              prefetch={false}
              className="text-accent underline"
              href={`/studio/?match=${session.match_id}`}
            >
              {t("landing.studio")}
            </Link>
            <Link
              prefetch={false}
              className="text-accent underline"
              href={`/fan/?match=${session.match_id}`}
            >
              {t("landing.fan")}
            </Link>
            <Link
              prefetch={false}
              className="text-accent underline"
              href={`/overlay/?match=${session.match_id}&lang=${lang}`}
            >
              {t("landing.overlay")}
            </Link>
          </span>
        </li>
      ))}
    </ul>
  );
}

function ScenarioCard({
  scenario,
  lang,
  create,
}: {
  scenario: ScenarioInfo;
  lang: "en" | "vi";
  create: (body: CreateBody) => Promise<MatchInfo | null>;
}) {
  const { t } = useT();
  const router = useRouter();
  const [seed, setSeed] = useState(scenario.default_seed);
  const [own, setOwn] = useState(false);
  const [busy, setBusy] = useState(false);

  const open = async (page: "studio" | "fan" | "overlay" | "compare") => {
    setBusy(true);
    const info = await create({
      scenario: scenario.key as CreateBody["scenario"],
      seed,
      speed: 20,
      autostart: page !== "studio" && page !== "overlay",
      ...(own ? { private: true } : {}),
    });
    setBusy(false);
    if (!info) return;
    const id = info.match_id; // a private session's id carries its token
    const query = page === "overlay" ? `?match=${id}&lang=${lang}` : `?match=${id}`;
    router.push(`/${page}/${query}`);
  };

  return (
    <article
      data-testid={`scenario-${scenario.key}`}
      className="flex flex-col gap-3 rounded-lg border border-line bg-panel p-4"
    >
      <div className="flex items-center gap-2">
        <TeamChip name={scenario.home.short_name} color={scenario.home.primary_color} />
        <span className="text-muted">vs</span>
        <TeamChip name={scenario.away.short_name} color={scenario.away.primary_color} />
      </div>
      <h3 className="font-semibold">{lang === "vi" ? scenario.title_vi : scenario.title_en}</h3>
      <p className="text-xs text-muted">
        {scenario.home.name} · {scenario.away.name}
      </p>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-2">
          {t("landing.seed")}
          <input
            type="number"
            min={0}
            max={1_000_000}
            value={seed}
            onChange={(event) =>
              setSeed(Math.max(0, Math.min(1_000_000, Number(event.target.value) || 0)))
            }
            className="w-24 rounded border border-line bg-bg px-2 py-1"
          />
        </label>
        <label className="flex items-center gap-2" title={t("landing.privateHint")}>
          <input
            type="checkbox"
            data-testid={`private-${scenario.key}`}
            checked={own}
            onChange={(event) => setOwn(event.target.checked)}
          />
          {t("landing.private")}
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        {(["studio", "fan", "overlay", "compare"] as const).map((page) => (
          <button
            key={page}
            type="button"
            disabled={busy}
            data-testid={`open-${page}-${scenario.key}`}
            onClick={() => void open(page)}
            className={`rounded px-3 py-1.5 text-sm font-semibold disabled:opacity-50 ${
              page === "studio"
                ? "bg-accent-fill text-white"
                : "border border-line hover:bg-panel-2"
            }`}
          >
            {t(`landing.${page}`)}
          </button>
        ))}
      </div>
    </article>
  );
}
