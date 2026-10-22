"use client";

// Why / Evidence / Agent Trace for a claim or a studio item (docs/day4-design.md section 5.9).
// Details are fetched when the drawer opens: the claim with its trace, or the canonical studio
// item (every language and register) with the trace of the job that made it.
import { useEffect, useMemo, useState } from "react";
import { Chip, Drawer, EmptyState, Tabs } from "@/components/common/ui";
import { api, type ApiError } from "@/lib/api";
import { formatMinute } from "@/lib/clock";
import type {
  ClaimResult,
  Fact,
  PlayerBrief,
  ScenarioInfo,
  StudioItemDetail,
  TraceSpan,
} from "@/lib/contracts";
import { kindLabel, useT, type Key } from "@/lib/i18n";
import type { MatchEvent } from "@/lib/ws-types";
import { playerName } from "@/hooks/usePlayers";
import { FactChips, VerdictPill } from "./ClaimBox";
import { PitchMap } from "./PitchMap";
import { TraceGantt } from "./TraceGantt";

export type DrawerTarget = { kind: "claim"; id: string } | { kind: "studio"; id: string };
type Tab = "why" | "evidence" | "trace";
type Loaded = { kind: "claim"; claim: ClaimResult } | { kind: "studio"; detail: StudioItemDetail };

const MAX_EVIDENCE = 200;

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function factsOf(loaded: Loaded): Fact[] {
  if (loaded.kind === "claim") {
    const all = [...loaded.claim.headline, ...loaded.claim.assertions.flatMap((a) => a.facts)];
    return [...new Map(all.map((fact) => [fact.key, fact])).values()];
  }
  return asArray<Fact>(loaded.detail.item.facts);
}

function evidenceOf(loaded: Loaded): string[] {
  return loaded.kind === "claim"
    ? loaded.claim.evidence_event_ids
    : asArray<string>(loaded.detail.item.evidence_event_ids);
}

function traceOf(loaded: Loaded): TraceSpan[] {
  return loaded.kind === "claim" ? [...(loaded.claim.trace ?? [])] : loaded.detail.trace;
}

type BodyProps = {
  matchId: string;
  target: DrawerTarget;
  homeId: string;
  scenario: ScenarioInfo | null;
  players: Map<string, PlayerBrief>;
};

export function ItemDrawer({
  target,
  onClose,
  ...rest
}: Omit<BodyProps, "target"> & { target: DrawerTarget | null; onClose: () => void }) {
  const { t } = useT();
  return (
    <Drawer
      open={target !== null}
      onClose={onClose}
      title={`${t("drawer.why")} · ${t("drawer.evidence")} · ${t("drawer.trace")}`}
    >
      {/* a new target is a new body: its state starts fresh */}
      {target ? <DrawerBody key={`${target.kind}:${target.id}`} target={target} {...rest} /> : null}
    </Drawer>
  );
}

function DrawerBody({ matchId, target, homeId, scenario, players }: BodyProps) {
  const { t, lang } = useT();
  const [tab, setTab] = useState<Tab>("why");
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [events, setEvents] = useState<{ found: MatchEvent[]; missing: number } | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      if (target.kind === "claim") {
        const result = await api.claim(target.id);
        if (!alive) return;
        if (result.ok) setLoaded({ kind: "claim", claim: result.data });
        else setError(result.error);
      } else {
        const result = await api.studioItem(matchId, target.id);
        if (!alive) return;
        if (result.ok) setLoaded({ kind: "studio", detail: result.data });
        else setError(result.error);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, [target, matchId]);

  const evidence = useMemo(() => (loaded ? evidenceOf(loaded) : []), [loaded]);
  useEffect(() => {
    if (!loaded || evidence.length === 0) return;
    let alive = true;
    void api.eventsById(matchId, evidence.slice(0, MAX_EVIDENCE)).then((result) => {
      if (alive && result.ok) {
        setEvents({
          found: result.data.events as MatchEvent[],
          missing: result.data.missing.length,
        });
      }
    });
    return () => {
      alive = false;
    };
  }, [loaded, evidence, matchId]);

  return (
    <>
      <Tabs<Tab>
        label={t("studio.details")}
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "why", label: t("drawer.why"), testId: "drawer-tab-why" },
          { value: "evidence", label: t("drawer.evidence"), testId: "drawer-tab-evidence" },
          { value: "trace", label: t("drawer.trace"), testId: "drawer-tab-trace" },
        ]}
      />
      {error ? (
        <p className="p-4 text-sm text-verdict-false">
          {t(`err.${error.code}`)} {error.message}
        </p>
      ) : null}
      {!loaded && !error ? <EmptyState>{t("drawer.loading")}</EmptyState> : null}
      {loaded && tab === "why" ? <Why loaded={loaded} /> : null}
      {loaded && tab === "evidence" ? (
        <div className="flex flex-col gap-3 p-4" data-testid="drawer-evidence">
          <section>
            <h3 className="mb-1 text-sm font-semibold">{t("drawer.facts")}</h3>
            <FactChips facts={factsOf(loaded)} max={30} />
          </section>
          <section>
            <h3 className="mb-1 text-sm font-semibold">{t("drawer.pitch")}</h3>
            <PitchMap
              events={events?.found ?? []}
              homeId={homeId}
              scenario={scenario}
              players={players}
            />
          </section>
          <section>
            <h3 className="mb-1 text-sm font-semibold">{t("drawer.evidenceEvents")}</h3>
            {evidence.length === 0 ? <EmptyState>{t("drawer.noEvidence")}</EmptyState> : null}
            {events && events.missing > 0 ? (
              <p className="text-xs text-muted">
                {t("drawer.missingEvents", { n: events.missing })}
              </p>
            ) : null}
            <ol
              className="max-h-64 overflow-auto text-xs"
              data-testid="evidence-events"
              tabIndex={0}
              aria-label={t("drawer.evidence")}
            >
              {(events?.found ?? []).map((event) => (
                <li key={event.id} className="flex gap-2 border-b border-line/50 py-0.5">
                  <span className="tabular w-10 text-right text-muted">
                    {formatMinute(event.period, event.clock_ms)}
                  </span>
                  <span>{kindLabel(lang, event.type ?? "")}</span>
                  {event.player ? (
                    <span className="text-muted">{playerName(players, event.player)}</span>
                  ) : null}
                  <span className="ml-auto font-mono text-muted">{event.id.split("-").pop()}</span>
                </li>
              ))}
            </ol>
          </section>
        </div>
      ) : null}
      {loaded && tab === "trace" ? <TraceGantt spans={traceOf(loaded)} /> : null}
    </>
  );
}

function Why({ loaded }: { loaded: Loaded }) {
  const { t, lang } = useT();
  if (loaded.kind === "claim") {
    const claim = loaded.claim;
    return (
      <div className="flex flex-col gap-3 p-4" data-testid="drawer-why">
        <div className="flex flex-wrap items-center gap-2">
          <VerdictPill verdict={claim.verdict} />
          <span className="text-sm text-muted">
            {t("studio.claimAsOf", { minute: claim.as_of_display })}
          </span>
        </div>
        <p className="text-lg font-semibold">“{claim.text}”</p>
        <p className="text-sm">
          <span className="font-semibold">{t("drawer.reason")}: </span>
          {t(`reason.${claim.reason_code}` as Key)}
        </p>
        <p className="text-sm">
          <span className="font-semibold">{t("drawer.explanation")}: </span>
          {claim.explanation}
        </p>
        <section>
          <h3 className="mb-1 text-sm font-semibold">{t("drawer.assertions")}</h3>
          <ul className="flex flex-col gap-2">
            {claim.assertions.map((assertion, index) => (
              <li key={index} className="rounded border border-line p-2" data-testid="assertion">
                <div className="mb-1 flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">
                    “{assertion.assertion.text_span ?? claim.text}”
                  </span>
                  <Chip tone={assertion.outcome === "SUPPORTED" ? "accent" : "warn"}>
                    {t(`outcome.${assertion.outcome}` as Key)}
                  </Chip>
                </div>
                <FactChips facts={assertion.facts} max={8} />
              </li>
            ))}
          </ul>
        </section>
        <p className="text-xs text-muted">
          {t("drawer.guardrail")}:{" "}
          {claim.guardrail.passed ? t("drawer.passed") : t("drawer.failed")}
          {(claim.guardrail.violations ?? []).map((v) => ` · ${v.code}`).join("")}
        </p>
        {claim.degraded.length ? (
          <p className="text-xs">
            {t("drawer.degraded")}:{" "}
            {claim.degraded.map((d) => t(`degraded.${d}` as Key)).join(", ")}
          </p>
        ) : null}
      </div>
    );
  }
  const item = loaded.detail.item;
  const texts = publishedTexts(loaded.detail);
  const degraded = asArray<string>(item.degraded);
  const minute = String(item.display_minute ?? item.match_clock ?? "");
  const trigger = String(item.trigger_kind ?? loaded.detail.kind);
  return (
    <div className="flex flex-col gap-3 p-4" data-testid="drawer-why">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="tabular font-bold">{minute}</span>
        <Chip>{loaded.detail.kind}</Chip>
        <span>
          {t("drawer.trigger")}: {kindLabel(lang, trigger)}
        </span>
      </div>
      <section>
        <h3 className="mb-1 text-sm font-semibold">{t("drawer.published")}</h3>
        <ul className="flex flex-col gap-1 text-sm">
          {texts.map((text) => (
            <li key={`${text.tag}:${text.text}`} className="flex gap-2">
              <span className="w-20 shrink-0 text-[11px] font-semibold uppercase text-muted">
                {text.tag}
              </span>
              <span>{text.text}</span>
            </li>
          ))}
        </ul>
      </section>
      {degraded.length ? (
        <p className="text-xs">
          {t("drawer.degraded")}: {degraded.map((d) => t(`degraded.${d}` as Key)).join(", ")}
        </p>
      ) : null}
    </div>
  );
}

type Published = { tag: string; text: string };

function publishedTexts(detail: StudioItemDetail): Published[] {
  const item = detail.item;
  const found: Published[] = [];
  for (const language of ["en", "vi"] as const) {
    for (const line of asArray<{ text: string }>(item[language])) {
      found.push({
        tag: `${language} · ${String(item[`source_${language}`] ?? "")}`,
        text: line.text,
      });
    }
  }
  // commentary versions carry `text`; recap versions a title and paragraphs
  type Version = {
    language: string;
    mode: string;
    text?: string;
    title?: string;
    paragraphs?: string[];
  };
  for (const text of asArray<Version>(item.texts)) {
    const body = text.text ?? [text.title, ...(text.paragraphs ?? [])].filter(Boolean).join(" ");
    found.push({ tag: `${text.language} · ${text.mode}`, text: body });
  }
  for (const language of ["en", "vi"] as const) {
    const title = item[`title_${language}`];
    const subtitle = item[`subtitle_${language}`];
    if (typeof title === "string")
      found.push({ tag: language, text: `${title} — ${String(subtitle ?? "")}` });
  }
  return found;
}
