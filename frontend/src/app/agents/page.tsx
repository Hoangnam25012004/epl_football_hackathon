"use client";

// /agents/?match=ID: Agent Ops (docs/day7-design.md section 2.5). The agents of the match at
// work, live: the two lanes, the shared state the runs read, the handoffs in words, a table of
// every agent, the latest span of the one picked, and Chaos (section 2.6) at the side.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ChaosPanel } from "@/components/agents/ChaosPanel";
import { Lanes } from "@/components/agents/Lanes";
import { PageShell } from "@/components/common/PageShell";
import { NoMatch } from "@/components/common/NoMatch";
import { SyntheticBadge } from "@/components/common/SyntheticBadge";
import { useToast } from "@/components/common/Toast";
import { Chip, EmptyState, LangToggle, Panel } from "@/components/common/ui";
import { useAgentOps } from "@/hooks/useAgentOps";
import { nodeKey, type AgentOpsState } from "@/lib/agent-ops";
import { api } from "@/lib/api";
import type { Health } from "@/lib/contracts";
import { hasKey, useT, type Key } from "@/lib/i18n";
import { rememberSession } from "@/lib/sessions";

export default function AgentsPage() {
  return (
    <PageShell defaultLang="en" theme="studio">
      <Agents />
    </PageShell>
  );
}

function Agents() {
  const { t } = useT();
  const router = useRouter();
  const toast = useToast();
  const matchId = useSearchParams().get("match");
  const { state, connection, refresh } = useAgentOps(matchId);
  const [selected, setSelected] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    void api.health().then((result) => result.ok && setHealth(result.data));
  }, []);

  if (!matchId) return <NoMatch />;

  const startDemo = async () => {
    const created = await api.createMatch({
      scenario: "dominant_loser",
      seed: 42,
      speed: 20,
      private: true,
    });
    if (!created.ok) {
      toast.show(t("landing.createFailed", { message: created.error.message }), "error");
      return;
    }
    rememberSession(created.data.match_id);
    router.push(`/agents/?match=${created.data.match_id}`);
  };

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-3 py-2">
        <Link href="/" className="font-extrabold">
          {t("app.name")}
        </Link>
        <h1 className="font-semibold">{t("agents.title")}</h1>
        <span className="text-xs text-muted">{matchId}</span>
        <nav className="ml-auto flex flex-wrap items-center gap-3 text-xs">
          <a className="underline" href={`/studio/?match=${matchId}`}>
            {t("agents.openStudio")} ↗
          </a>
          <Chip tone={connection === "open" ? "neutral" : "warn"}>
            {t(connection === "open" ? "agents.live" : "agents.reconnecting")}
          </Chip>
          <LangToggle />
          <SyntheticBadge />
        </nav>
      </header>
      {connection === "unknown" ? (
        <NoMatch />
      ) : (
        <main className="grid flex-1 gap-3 p-3 lg:grid-cols-[1fr_20rem]">
          <div className="flex min-w-0 flex-col gap-3">
            <p className="text-sm text-muted">{t("agents.subtitle")}</p>
            <Panel title={t("agents.title")} testId="panel-lanes">
              <div className="p-3">
                <Lanes state={state} selected={selected} onSelect={setSelected} />
              </div>
            </Panel>
            <div className="grid gap-3 md:grid-cols-2">
              <Panel title={t("agents.sharedState")} testId="panel-shared">
                <SharedState state={state} />
              </Panel>
              <Panel title={t("agents.detail")} testId="panel-detail">
                <Detail state={state} selected={selected} />
              </Panel>
            </div>
            <Panel title={t("agents.log")} testId="panel-log">
              <HandoffLog state={state} />
            </Panel>
            <Panel title={t("agents.table")} testId="panel-agents">
              <AgentTable state={state} onSelect={setSelected} />
            </Panel>
          </div>
          <Panel title={t("chaos.title")} testId="panel-chaos" className="self-start">
            <ChaosPanel
              matchId={matchId}
              chaos={state.chaos}
              studioTemplates={health?.llm_live_scope === "claims"}
              onChanged={() => void refresh()}
              onStartDemo={() => void startDemo()}
            />
          </Panel>
        </main>
      )}
    </div>
  );
}

function SharedState({ state }: { state: AgentOpsState }) {
  const { t } = useT();
  const breakers = Object.entries(state.breakers);
  return (
    <div className="flex flex-col gap-2 p-3 text-sm" data-testid="shared-state">
      <p className="font-mono">
        {state.shared
          ? t("agents.sharedStateAt", { seq: state.shared.seq, minute: state.shared.minute })
          : t("agents.noState")}
      </p>
      {state.shared ? <p className="text-xs text-muted">{state.shared.runId}</p> : null}
      <div className="flex flex-wrap gap-1">
        {breakers.map(([workflow, breaker]) => (
          <Chip key={workflow} tone={breaker === "closed" ? "neutral" : "warn"}>
            {workflow} · {t(`agents.breaker.${breaker}` as Key)}
          </Chip>
        ))}
      </div>
    </div>
  );
}

function HandoffLog({ state }: { state: AgentOpsState }) {
  const { t } = useT();
  if (!state.log.length) return <EmptyState>{t("agents.empty")}</EmptyState>;
  return (
    <div className="flex flex-col gap-1 p-3">
      {state.dropped ? (
        <p className="text-xs text-muted">{t("agents.dropped", { n: state.dropped })}</p>
      ) : null}
      <ol aria-live="polite" className="flex flex-col gap-0.5 text-xs" data-testid="handoff-log">
        {[...state.log].reverse().map((line) => (
          <li
            key={line.seq}
            className={line.tone === "warn" ? "text-amber-800 dark:text-amber-300" : ""}
          >
            <span className="text-muted">{line.workflow} · </span>
            {t(line.key, line.vars)}
          </li>
        ))}
      </ol>
    </div>
  );
}

function AgentTable({
  state,
  onSelect,
}: {
  state: AgentOpsState;
  onSelect: (key: string) => void;
}) {
  const { t } = useT();
  const rows = Object.values(state.stats);
  if (!rows.length) return <EmptyState>{t("agents.empty")}</EmptyState>;
  return (
    <div className="overflow-x-auto p-3">
      <table className="w-full text-left text-xs" data-testid="agent-table">
        <thead className="text-muted">
          <tr>
            <th className="py-1 pr-2">{t("agents.colAgent")}</th>
            <th className="py-1 pr-2">{t("agents.colRole")}</th>
            <th className="py-1 pr-2">{t("agents.colCalls")}</th>
            <th className="py-1 pr-2">{t("agents.colP95")}</th>
            <th className="py-1 pr-2">{t("agents.colSource")}</th>
            <th className="py-1 pr-2">{t("agents.colLast")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const role = `role.${row.agent}`;
            return (
              <tr key={nodeKey(row.workflow, row.agent)} className="border-t border-line">
                <td className="py-1 pr-2">
                  <button
                    type="button"
                    className="font-semibold underline"
                    onClick={() => onSelect(nodeKey(row.workflow, row.agent))}
                  >
                    {row.agent}
                  </button>
                  <span className="text-muted"> · {row.workflow}</span>
                </td>
                <td className="py-1 pr-2">{hasKey(role) ? t(role) : ""}</td>
                <td className="tabular py-1 pr-2">{row.calls}</td>
                <td className="tabular py-1 pr-2">
                  {row.p95_ms === null || row.p95_ms === undefined
                    ? "–"
                    : `${Math.round(row.p95_ms)} ms`}
                </td>
                <td className="py-1 pr-2">
                  {Object.entries(row.sources)
                    .map(([source, n]) => {
                      const key = `agents.src.${source}`;
                      return `${hasKey(key) ? t(key) : source} ${n}`;
                    })
                    .join(" · ")}
                </td>
                <td className="py-1 pr-2">
                  {row.last_status ? t(`agents.state.${row.last_status}` as Key) : "–"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ state, selected }: { state: AgentOpsState; selected: string | null }) {
  const { t } = useT();
  const stat = selected ? state.stats[selected] : undefined;
  if (!stat) return <EmptyState>{t("agents.pick")}</EmptyState>;
  return (
    <div className="flex flex-col gap-2 p-3 text-xs" data-testid="agent-detail">
      <p className="font-semibold">
        {stat.agent} · {stat.last_run_id}
      </p>
      <h4 className="font-semibold text-muted">{t("agents.input")}</h4>
      <pre className="max-h-40 overflow-auto rounded bg-panel-2 p-2 whitespace-pre-wrap">
        {JSON.stringify(stat.last_input, null, 2)}
      </pre>
      <h4 className="font-semibold text-muted">{t("agents.output")}</h4>
      <pre className="max-h-40 overflow-auto rounded bg-panel-2 p-2 whitespace-pre-wrap">
        {JSON.stringify(stat.last_output, null, 2)}
      </pre>
    </div>
  );
}
