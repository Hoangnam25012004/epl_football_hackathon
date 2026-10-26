"use client";

// The two lanes of Agent Ops (docs/day7-design.md section 2.5): the claim workflow, the studio
// workflow and the narrator. A node's state is an icon and words, never a colour alone; the
// edge after a node names the typed message it hands on. At 390 px the lanes are vertical lists.
import { EDGES, LANES, nodeKey, type AgentOpsState, type NodeStatus } from "@/lib/agent-ops";
import { useT, type Key } from "@/lib/i18n";

const ICON: Record<NodeStatus, string> = {
  idle: "○",
  running: "◐",
  ok: "✓",
  fallback: "↺",
  rejected: "⛔",
  error: "✕",
  retry: "↻",
};

const TONE: Record<NodeStatus, string> = {
  idle: "border-line text-muted",
  running: "border-accent text-accent node-running",
  ok: "border-line",
  fallback: "border-amber-500/60",
  rejected: "border-verdict-false",
  error: "border-verdict-false",
  retry: "border-amber-500/60",
};

export function Lanes({
  state,
  selected,
  onSelect,
}: {
  state: AgentOpsState;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const { t } = useT();
  return (
    <div className="flex flex-col gap-4" data-testid="agent-lanes">
      {LANES.map((lane) => (
        <section key={lane.id} aria-label={t(lane.title)} className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">{t(lane.title)}</h3>
          <ol className="flex flex-col gap-1 sm:flex-row sm:flex-wrap sm:items-stretch">
            {lane.nodes.map(({ workflow, agent }) => {
              const key = nodeKey(workflow, agent);
              const node = state.nodes[key];
              const status: NodeStatus = node?.status ?? "idle";
              const edge = EDGES[key];
              return (
                <li key={key} className="flex flex-col gap-1 sm:flex-row sm:items-center">
                  <button
                    type="button"
                    data-testid="agent-node"
                    data-agent={agent}
                    data-workflow={workflow}
                    data-status={status}
                    aria-pressed={selected === key}
                    onClick={() => onSelect(key)}
                    className={`flex min-w-32 flex-col rounded-md border-2 bg-panel px-2 py-1 text-left text-xs ${TONE[status]} ${selected === key ? "ring-2 ring-accent" : ""}`}
                  >
                    <span className="font-semibold">{agent}</span>
                    <span>
                      <span aria-hidden>{ICON[status]} </span>
                      {t(`agents.state.${status}` as Key)}
                      {node?.durationMs ? ` · ${Math.round(node.durationMs)} ms` : ""}
                    </span>
                    {node?.source ? (
                      <span className="text-muted">{t(`agents.src.${node.source}` as Key)}</span>
                    ) : null}
                  </button>
                  {edge ? (
                    <span
                      data-testid="agent-edge"
                      className="px-1 font-mono text-[10px] text-muted sm:max-w-28"
                    >
                      → {edge}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}
