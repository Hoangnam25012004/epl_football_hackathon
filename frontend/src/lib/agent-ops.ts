// Agent Ops (docs/day7-design.md section 2.5): the state of the page, built from the `hello`
// snapshot and the batches of /ws/agents/{id}. Pure: a reducer and the layout of the lanes.
import type {
  AgentEvent,
  AgentOpsMessage,
  AgentOpsSnapshot,
  AgentStat,
  ChaosStatus,
} from "@/lib/contracts";
import type { Line } from "@/lib/claim-source";

export type Workflow = AgentEvent["workflow"];
export type NodeStatus = "idle" | "running" | NonNullable<AgentEvent["status"]>;

/** The two lanes and their agents, in the order a run reaches them. */
export const LANES: { id: "claim" | "studio"; title: "agents.claimLane" | "agents.studioLane"; nodes: { workflow: Workflow; agent: string }[] }[] = [
  {
    id: "claim",
    title: "agents.claimLane",
    nodes: [
      "ClaimIntake", "ClaimAnalyst", "RuleAnalyst", "EvidenceCollector", "Judge", "Explainer",
      "Guardrail", "Publisher",
    ].map((agent) => ({ workflow: "claim" as const, agent })),
  },
  {
    id: "studio",
    title: "agents.studioLane",
    nodes: [
      ...["StudioIntake", "FactPackBuilder", "TalkingPointWriter", "GraphicsDesigner",
        "Commentator", "Guardrail", "StudioPublisher"].map((agent) => ({
        workflow: "studio" as const,
        agent,
      })),
      { workflow: "narrator" as const, agent: "Narrator" },
    ],
  },
]; // prettier-ignore

/** The typed message each agent hands on (the edge after its node). */
export const EDGES: Record<string, string> = {
  "claim:ClaimIntake": "ClaimContext",
  "claim:ClaimAnalyst": "AnalystOutcome",
  "claim:RuleAnalyst": "ClaimAnalysis",
  "claim:EvidenceCollector": "EvidenceBundle",
  "claim:Judge": "ExplainRequest | TemplateRequest",
  "claim:Explainer": "DraftForReview",
  "claim:Guardrail": "Publication",
  "studio:StudioIntake": "StudioPlan",
  "studio:FactPackBuilder": "FactPack",
  "studio:TalkingPointWriter": "BranchResult",
  "studio:GraphicsDesigner": "BranchResult",
  "studio:Commentator": "BranchResult",
  "studio:StudioPublisher": "StudioOutput",
};

export type NodeState = {
  status: NodeStatus;
  runId: string | null;
  durationMs: number | null;
  source: AgentEvent["source"];
};

export type LogLine = Line & { seq: number; workflow: Workflow };

export type AgentOpsState = {
  matchId: string | null;
  nodes: Record<string, NodeState>;
  stats: Record<string, AgentStat>;
  log: LogLine[];
  shared: { seq: number; minute: string; runId: string } | null;
  breakers: Record<string, NonNullable<AgentEvent["breaker"]>>;
  chaos: ChaosStatus | null;
  dropped: number;
  lastSeq: number;
};

export const LOG_LINES = 60;

export function nodeKey(workflow: Workflow, agent: string): string {
  return `${workflow}:${agent}`;
}

export const EMPTY: AgentOpsState = {
  matchId: null,
  nodes: {},
  stats: {},
  log: [],
  shared: null,
  breakers: {},
  chaos: null,
  dropped: 0,
  lastSeq: 0,
};

/** The handoff log in plain words: what a run handed on, what was blocked, what fell back. */
export function logLine(event: AgentEvent): LogLine | null {
  const base = { seq: event.seq, workflow: event.workflow };
  if (event.phase !== "end") return null;
  if (event.agent === "CircuitBreaker")
    return {
      ...base,
      key: "agents.logBreaker",
      vars: { workflow: event.workflow, state: event.breaker ?? event.name },
      tone: event.breaker === "closed" ? undefined : "warn",
    };
  if (event.status === "rejected")
    return {
      ...base,
      key: "agents.logRejected",
      vars: { codes: (event.guardrail ?? []).join(", ") || "?" },
      tone: "warn",
    };
  if (event.workflow === "narrator") return { ...base, key: "agents.logCue" };
  if (event.status === "fallback" || event.kind === "fallback")
    return {
      ...base,
      key: "agents.logFallback",
      vars: { agent: event.agent, source: event.source ?? event.name },
      tone: "warn",
    };
  if (event.kind === "agent" && event.handoff)
    return {
      ...base,
      key: "agents.logHandoff",
      vars: { agent: event.agent, handoff: event.handoff },
    };
  return null;
}

function applyEvent(state: AgentOpsState, event: AgentEvent): void {
  if (event.seq <= state.lastSeq) return; // seen in the snapshot already
  state.lastSeq = event.seq;
  const key = nodeKey(event.workflow, event.agent);
  // an llm span runs inside its agent: it lights the same node
  const previous = state.nodes[key];
  state.nodes[key] = {
    status: event.phase === "start" ? "running" : (event.status ?? "ok"),
    runId: event.run_id,
    durationMs: event.duration_ms ?? previous?.durationMs ?? null,
    source: event.source ?? previous?.source ?? null,
  };
  if (event.breaker) state.breakers[event.workflow] = event.breaker;
  if (event.as_of_seq !== null && event.as_of_seq !== undefined)
    state.shared = {
      seq: event.as_of_seq,
      minute: event.as_of_display ?? "",
      runId: event.run_id,
    };
  const line = logLine(event);
  if (line) {
    state.log.push(line);
    if (state.log.length > LOG_LINES) state.log.splice(0, state.log.length - LOG_LINES);
  }
}

function fromSnapshot(snapshot: AgentOpsSnapshot): AgentOpsState {
  const state: AgentOpsState = {
    ...EMPTY,
    matchId: snapshot.match_id,
    nodes: {},
    log: [],
    stats: Object.fromEntries(snapshot.agents.map((s) => [nodeKey(s.workflow, s.agent), s])),
    breakers: { ...snapshot.breakers },
    chaos: snapshot.chaos ?? null,
  };
  for (const event of snapshot.recent) applyEvent(state, event);
  // a node the snapshot saw ended, not still running: the hello may cut a run in two
  for (const node of Object.values(state.nodes)) if (node.status === "running") node.status = "ok";
  return state;
}

export function reduce(state: AgentOpsState, message: AgentOpsMessage): AgentOpsState {
  if (message.type === "hello") return fromSnapshot(message.payload as AgentOpsSnapshot);
  const batch = message.payload as { events: AgentEvent[]; dropped?: number };
  const next: AgentOpsState = {
    ...state,
    nodes: { ...state.nodes },
    log: [...state.log],
    breakers: { ...state.breakers },
    dropped: state.dropped + (batch.dropped ?? 0),
  };
  for (const event of batch.events) applyEvent(next, event);
  return next;
}

/** A fresh snapshot (GET /agents): the statistics and the chaos, the live nodes kept. */
export function withSnapshot(state: AgentOpsState, snapshot: AgentOpsSnapshot): AgentOpsState {
  return {
    ...state,
    stats: Object.fromEntries(snapshot.agents.map((s) => [nodeKey(s.workflow, s.agent), s])),
    breakers: { ...state.breakers, ...snapshot.breakers },
    chaos: snapshot.chaos ?? null,
  };
}
