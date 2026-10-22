"use client";

// "/": pick a scenario and a seed, open the studio, the fan player, the overlay or the comparison
// (docs/day4-design.md section 9).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PageShell } from "@/components/common/PageShell";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { useToast } from "@/components/common/Toast";
import { Chip, LangToggle, TeamChip } from "@/components/common/ui";
import { api } from "@/lib/api";
import type { Health, MatchInfo, ScenarioInfo } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import { useScenarios } from "@/hooks/useScenarios";

export default function LandingPage() {
  return (
    <PageShell defaultLang="en">
      <Landing />
    </PageShell>
  );
}

function Landing() {
  const { t, lang } = useT();
  const scenarios = useScenarios();
  const [sessions, setSessions] = useState<MatchInfo[] | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      const [matches, server] = await Promise.all([api.matches(), api.health()]);
      if (!alive) return;
      if (matches.ok) setSessions(matches.data);
      if (server.ok) setHealth(server.data);
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

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
      {health ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>{t("landing.server")}:</span>
          <Chip tone="accent">
            {t("landing.llm", { provider: health.llm_provider, model: health.model })}
          </Chip>
          {health.degraded.map((flag) => (
            <Chip key={flag} tone="warn">
              {flag}
            </Chip>
          ))}
        </div>
      ) : null}
      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-bold">{t("landing.title")}</h2>
        <p className="text-sm text-muted">{t("landing.subtitle")}</p>
        <div className="grid gap-3 md:grid-cols-2">
          {(scenarios ?? []).map((scenario) => (
            <ScenarioCard key={scenario.key} scenario={scenario} lang={lang} />
          ))}
        </div>
        {scenarios === null ? <p className="text-sm text-muted">{t("common.loading")}</p> : null}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-lg font-bold">{t("landing.sessions")}</h2>
        {sessions && sessions.length === 0 ? (
          <p className="text-sm text-muted">{t("landing.noSessions")}</p>
        ) : null}
        <ul className="flex flex-col gap-1">
          {(sessions ?? []).map((session) => (
            <li
              key={session.match_id}
              className="flex flex-wrap items-center gap-2 rounded border border-line bg-panel px-3 py-2 text-sm"
            >
              <span className="font-mono">{session.match_id}</span>
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
              </span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function ScenarioCard({ scenario, lang }: { scenario: ScenarioInfo; lang: "en" | "vi" }) {
  const { t } = useT();
  const toast = useToast();
  const router = useRouter();
  const [seed, setSeed] = useState(scenario.default_seed);
  const [busy, setBusy] = useState(false);
  const matchId = `${scenario.key}-${seed}`;

  const open = async (page: "studio" | "fan" | "overlay" | "compare") => {
    setBusy(true);
    const result = await api.createMatch({
      scenario: scenario.key as Parameters<typeof api.createMatch>[0]["scenario"],
      seed,
      speed: 20,
      autostart: page !== "studio" && page !== "overlay",
    });
    setBusy(false);
    if (!result.ok) {
      toast.show(t("landing.createFailed", { message: result.error.message }), "error");
      return;
    }
    const query = page === "overlay" ? `?match=${matchId}&lang=${lang}` : `?match=${matchId}`;
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
      <label className="flex items-center gap-2 text-sm">
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
