"use client";

// "/": the way in for judges and fans (docs/day6-design.md section 8.5): what the app does, one
// button for a private demo match (section 6.5), three steps with a link each, what to try and
// "my matches"; every scenario and seed under "Advanced" (docs/day4-design.md section 9).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { PageShell } from "@/components/common/PageShell";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { useToast } from "@/components/common/Toast";
import { Chip, LangToggle, TeamChip } from "@/components/common/ui";
import { api, type ApiError } from "@/lib/api";
import type { CreateMatch, Health, MatchInfo, ScenarioInfo } from "@/lib/contracts";
import { hasKey, useT } from "@/lib/i18n";
import { REPO_URL, VIDEO_URL } from "@/lib/links";
import { serverStatus } from "@/lib/server-status";
import { forgetSession, mySessions, rememberSession } from "@/lib/sessions";
import { useScenarios } from "@/hooks/useScenarios";

type CreateBody = Partial<CreateMatch> & { scenario: CreateMatch["scenario"] };
type DemoPage = "studio" | "fan" | "overlay" | "agents";

// The demo match: "Dominated but lost" at 20x, a private session of the viewer's own
const DEMO: CreateBody = { scenario: "dominant_loser", seed: 42, speed: 20, private: true };
const TRY = [
  "landing.try1",
  "landing.try2",
  "landing.try3",
  "landing.try4",
  "landing.try5",
  "landing.try6",
  "landing.try7",
] as const;

export default function LandingPage() {
  return (
    <PageShell defaultLang="browser">
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

function demoUrl(page: DemoPage, id: string, lang: "en" | "vi"): string {
  // a judge opens the overlay in a browser: `debug` shows it over a backdrop
  return page === "overlay"
    ? `/overlay/?match=${id}&lang=${lang}&debug=1`
    : `/${page}/?match=${id}`;
}

function Landing() {
  const { t, lang } = useT();
  const router = useRouter();
  const createMatch = useCreate();
  const scenarios = useScenarios();
  const [sessions, setSessions] = useState<MatchInfo[] | null>(null);
  const [mine, setMine] = useState<MatchInfo[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      const [matches, server] = await Promise.all([api.matches(), api.health()]);
      // my private sessions: still there, or forgotten once the server removed them
      const asked = mySessions().map((s) => s.id);
      const remembered = await Promise.all(asked.map((id) => api.match(id)));
      if (!alive) return;
      if (matches.ok) setSessions(matches.data);
      if (server.ok) setHealth(server.data);
      const kept: MatchInfo[] = [];
      remembered.forEach((result, index) => {
        if (result.ok) kept.push(result.data.info);
        else if (result.error.code === "not_found") forgetSession(asked[index] ?? "");
      });
      // a session made while this refresh was on its way is kept, not dropped
      const since = mySessions()
        .map((s) => s.id)
        .filter((id) => !asked.includes(id));
      setMine((current) => [...current.filter((m) => since.includes(m.match_id)), ...kept]);
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  /** A new private session is one of "my matches" at once, not at the next refresh: a second
   * step must open the same match, not make another. */
  const create = useCallback(
    async (body: CreateBody) => {
      const info = await createMatch(body);
      if (info?.private)
        setMine((list) => [info, ...list.filter((m) => m.match_id !== info.match_id)]);
      return info;
    },
    [createMatch],
  );

  /** "Start my demo match": always a new private session, opened paused in the studio. */
  const startDemo = async () => {
    setBusy(true);
    const info = await create({ ...DEMO, autostart: false });
    setBusy(false);
    if (info) router.push(demoUrl("studio", info.match_id, lang));
  };

  /** A step's page for the viewer's demo match: the newest of "my matches", or a new one. The
   * fan player and the overlay have no play button, so there the match is started. */
  const openStep = async (page: DemoPage) => {
    setBusy(true);
    let info: MatchInfo | null = mine[0] ?? null;
    if (!info) {
      info = await create({ ...DEMO, autostart: page !== "studio" });
    } else if (page !== "studio" && info.status === "created") {
      const started = await api.start(info.match_id);
      if (started.ok) {
        const running = started.data;
        info = running;
        setMine((list) => list.map((m) => (m.match_id === running.match_id ? running : m)));
      }
    }
    setBusy(false);
    if (info) router.push(demoUrl(page, info.match_id, lang));
  };

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex max-w-2xl flex-col gap-1">
          <h1 className="text-2xl font-extrabold">{t("app.name")}</h1>
          <p data-testid="intro">{t("landing.intro")}</p>
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
          disabled={busy}
          onClick={() => void startDemo()}
          className="rounded bg-accent-fill px-4 py-2 font-bold text-white disabled:opacity-50"
        >
          ▶ {t("landing.startDemo")}
        </button>
        <p className="text-sm text-muted">{t("landing.startDemoHint")}</p>
      </section>
      {health ? <ServerHealth health={health} /> : null}
      <section data-testid="demo-steps" className="flex flex-col gap-3">
        <h2 className="text-lg font-bold">{t("landing.stepsTitle")}</h2>
        <ol className="grid gap-3 md:grid-cols-3">
          <Step
            n={1}
            page="studio"
            title={t("landing.step1Title")}
            text={t("landing.step1Text")}
            go={t("landing.step1Go")}
            busy={busy}
            onOpen={openStep}
          />
          <Step
            n={2}
            page="fan"
            title={t("landing.step2Title")}
            text={t("landing.step2Text")}
            go={t("landing.step2Go")}
            busy={busy}
            onOpen={openStep}
          />
          <Step
            n={3}
            page="overlay"
            title={t("landing.step3Title")}
            text={t("landing.step3Text")}
            go={t("landing.step3Go")}
            busy={busy}
            onOpen={openStep}
          >
            <McpAddress health={health} />
          </Step>
        </ol>
      </section>
      <section data-testid="what-to-try" className="flex flex-col gap-2">
        <h2 className="text-lg font-bold">{t("landing.tryTitle")}</h2>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
          {TRY.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
        <button
          type="button"
          data-testid="open-agents"
          disabled={busy}
          onClick={() => void openStep("agents")}
          className="self-start rounded border border-line px-3 py-1.5 text-sm font-semibold text-accent hover:bg-panel-2 disabled:opacity-50"
        >
          {t("landing.agents")} →
        </button>
      </section>
      {mine.length ? (
        <section className="flex flex-col gap-2" data-testid="my-sessions">
          <h2 className="text-lg font-bold">{t("landing.mySessions")}</h2>
          <SessionList sessions={mine} lang={lang} />
        </section>
      ) : null}
      <details data-testid="advanced" className="rounded-lg border border-line bg-panel p-4">
        <summary className="cursor-pointer text-lg font-bold">{t("landing.advanced")}</summary>
        <div className="mt-4 flex flex-col gap-6">
          <section className="flex flex-col gap-3">
            <h2 className="text-lg font-bold">{t("landing.title")}</h2>
            <p className="text-sm text-muted">{t("landing.subtitle")}</p>
            <div className="grid gap-3 md:grid-cols-2">
              {(scenarios ?? []).map((scenario) => (
                <ScenarioCard key={scenario.key} scenario={scenario} lang={lang} create={create} />
              ))}
            </div>
            {scenarios === null ? (
              <p className="text-sm text-muted">{t("common.loading")}</p>
            ) : null}
          </section>
          <section className="flex flex-col gap-2">
            <h2 className="text-lg font-bold">{t("landing.sessions")}</h2>
            {sessions && sessions.length === 0 ? (
              <p className="text-sm text-muted">{t("landing.noSessions")}</p>
            ) : null}
            <SessionList sessions={sessions ?? []} lang={lang} />
          </section>
        </div>
      </details>
      <footer
        data-testid="project-links"
        className="flex flex-wrap gap-4 border-t border-line pt-4 text-sm"
      >
        <ProjectLink label={t("landing.source")} url={REPO_URL} />
        <ProjectLink label={t("landing.video")} url={VIDEO_URL} />
      </footer>
    </main>
  );
}

function ServerHealth({ health }: { health: Health }) {
  const { t } = useT();
  return (
    <div
      data-testid="server-health"
      className="flex flex-wrap items-center gap-2 text-xs text-muted"
    >
      <span>{t("landing.server")}:</span>
      {serverStatus(health).map((line) => (
        <Chip key={line.key} tone={line.tone ?? "neutral"}>
          {t(line.key, line.vars)}
        </Chip>
      ))}
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
  );
}

function Step({
  n,
  page,
  title,
  text,
  go,
  busy,
  onOpen,
  children,
}: {
  n: number;
  page: DemoPage;
  title: string;
  text: string;
  go: string;
  busy: boolean;
  onOpen: (page: DemoPage) => Promise<void>;
  children?: ReactNode;
}) {
  return (
    <li
      data-testid={`step-${page}`}
      className="flex flex-col gap-2 rounded-lg border border-line bg-panel p-4"
    >
      <h3 className="flex items-center gap-2 font-semibold">
        <span
          aria-hidden
          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-fill text-xs font-bold text-white"
        >
          {n}
        </span>
        {title}
      </h3>
      <p className="text-sm text-muted">{text}</p>
      {children}
      <button
        type="button"
        data-testid={`step-open-${page}`}
        disabled={busy}
        onClick={() => void onOpen(page)}
        className="mt-auto self-start rounded border border-line px-3 py-1.5 text-sm font-semibold text-accent hover:bg-panel-2 disabled:opacity-50"
      >
        {go} →
      </button>
    </li>
  );
}

/** The MCP endpoint of this server, to paste into an AI assistant (docs/mcp.md). */
function McpAddress({ health }: { health: Health | null }) {
  const { t } = useT();
  const [copied, setCopied] = useState(false);
  const mcp = health?.mcp;
  if (!health) return null;
  if (!mcp?.enabled || mcp.status !== "ok") {
    return (
      <p data-testid="mcp-off" className="text-xs text-muted">
        {t("landing.mcpOff")}
      </p>
    );
  }
  const url = `${window.location.origin}${mcp.path}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* no clipboard (an insecure origin): the address can still be selected */
    }
  };
  return (
    <div data-testid="mcp-url" className="flex items-center gap-2 text-xs">
      <code className="min-w-0 flex-1 rounded bg-bg px-2 py-1 break-all">{url}</code>
      <button
        type="button"
        onClick={() => void copy()}
        className="rounded border border-line px-2 py-1 hover:bg-panel-2"
      >
        {copied ? t("landing.copied") : t("landing.copy")}
      </button>
    </div>
  );
}

function ProjectLink({ label, url }: { label: string; url: string }) {
  const { t } = useT();
  if (!url) {
    return (
      <span className="text-muted">
        {label}: {t("landing.soon")}
      </span>
    );
  }
  return (
    <a className="text-accent underline" href={url} target="_blank" rel="noreferrer">
      {label} ↗
    </a>
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
      className="flex flex-col gap-3 rounded-lg border border-line bg-bg p-4"
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
