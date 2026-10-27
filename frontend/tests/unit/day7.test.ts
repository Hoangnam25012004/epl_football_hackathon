// Day 7 (docs/day7-design.md sections 2.4, 2.5, 2.9): a progressive claim in words, the
// server's status line without "mock", the Agent Ops reducer, and a store that never lets an
// older revision of a claim replace a newer one.
import { describe, expect, it } from "vitest";
import { EMPTY, LOG_LINES, logLine, nodeKey, reduce } from "@/lib/agent-ops";
import { claimView } from "@/lib/claim-source";
import type { AgentEvent, AgentOpsSnapshot, ClaimResult, Health } from "@/lib/contracts";
import { en } from "@/lib/i18n/en";
import { vi } from "@/lib/i18n/vi";
import { serverStatus } from "@/lib/server-status";
import { createMatchStore } from "@/store/match-store";

function claim(extra: Partial<ClaimResult> = {}): ClaimResult {
  return {
    claim_id: "m-c0001",
    match_id: "m",
    as_of_seq: 2000,
    as_of_display: "90+6'",
    text: "HAR áp đảo hiệp 2",
    language: "vi",
    verdict: "TRUE",
    reason_code: "ok",
    assertions: [],
    headline: [],
    explanation: "x",
    explanation_source: "template",
    analyst_source: "rule",
    model: "qwen2.5:1.5b",
    evidence_event_ids: [],
    guardrail: { passed: true, violations: [] },
    degraded: [],
    timings_ms: { total: 120 },
    trace: null,
    ...extra,
  };
}

describe("claimView", () => {
  it("a one-pass claim names the model or the rules", () => {
    expect(claimView(claim()).chips.map((c) => c.key)).toEqual([
      "claim.rulesTemplate",
      "claim.readRules",
    ]);
    const llm = claimView(claim({ explanation_source: "llm", analyst_source: "llm" }));
    expect(llm.chips[0]).toMatchObject({
      key: "claim.modelOnePass",
      vars: { model: "qwen2.5:1.5b" },
    });
    expect(llm.progress).toBeNull();
  });

  it("rev.1 says the rules answered, then shows the model's progress", () => {
    const queued = claimView(claim({ revision: 1, model_pass: "queued" }));
    expect(queued.chips[0]).toMatchObject({ key: "claim.rules", vars: { s: "0.1" } });
    expect(queued.progress?.key).toBe("claim.queued");
    expect(claimView(claim({ revision: 1, model_pass: "running" })).progress?.key).toBe(
      "claim.running",
    );
  });

  it("rev.2 by the model: its name, time, and how it read the claim", () => {
    const rev2 = claim({
      revision: 2,
      model_pass: "done",
      rev1_verdict: "TRUE",
      explanation_source: "llm",
      analyst_source: "llm",
      timings_ms: { total: 21000, model_pass: 21400 },
    });
    const view = claimView(rev2);
    expect(view.chips[0]).toMatchObject({
      key: "claim.model",
      vars: { model: "qwen2.5:1.5b", s: "21.4" },
    });
    expect(view.notes.map((n) => n.key)).toEqual(["claim.agree"]);
    const kept = claimView({ ...rev2, degraded: ["analyst_disagreement"] });
    expect(kept.notes.map((n) => n.key)).toEqual(["claim.disagree"]);
  });

  it("a skipped model pass keeps rev.1 and says why", () => {
    for (const reason of ["busy", "circuit_open", "no_answer", "rejected"] as const) {
      const view = claimView(claim({ revision: 1, model_pass: "skipped", skip_reason: reason }));
      expect(view.chips.map((c) => c.key)).toContain(`claim.skip.${reason}`);
      expect(view.progress).toBeNull();
    }
  });

  it("a verdict the model's reading changed is said in words", () => {
    const view = claimView(
      claim({
        revision: 2,
        model_pass: "done",
        rev1_verdict: "INSUFFICIENT_DATA",
        verdict: "TRUE",
        analyst_source: "llm",
        explanation_source: "llm",
      }),
    );
    expect(view.notes.at(-1)).toMatchObject({
      key: "claim.updated",
      vars: { from: "INSUFFICIENT_DATA", to: "TRUE" },
    });
  });
});

describe("serverStatus", () => {
  const base: Health = {
    status: "ok",
    llm_provider: "mock",
    model: "mock",
    degraded: [],
    sessions: 0,
    synthetic: true,
  };

  it("never says mock: the stand-in is called what it is", () => {
    const keys = serverStatus(base).map((l) => l.key);
    expect(keys).toEqual(["landing.modelMock"]);
    for (const key of keys) {
      expect(en[key].toLowerCase()).not.toContain("mock");
      expect(vi[key].toLowerCase()).not.toContain("mock");
    }
  });

  it("the Space: a CPU model for claims, templates for the studio, the browser's voice", () => {
    const space: Health = {
      ...base,
      llm_provider: "ollama",
      model: "qwen2.5:1.5b",
      llm_live_scope: "claims",
      claim_progressive: true,
      speech: { provider: "browser" } as Health["speech"],
    };
    expect(serverStatus(space).map((l) => l.key)).toEqual([
      "landing.modelCpu",
      "landing.studioTemplates",
      "landing.progressive",
      "landing.voiceBrowser",
    ]);
  });
});

function event(extra: Partial<AgentEvent>): AgentEvent {
  return {
    seq: 1,
    run_id: "m-c0001",
    workflow: "claim",
    agent: "ClaimIntake",
    kind: "agent",
    name: "intake",
    phase: "start",
    guardrail: [],
    ...extra,
  };
}

function snapshot(recent: AgentEvent[] = []): AgentOpsSnapshot {
  return { match_id: "m", synthetic: true, agents: [], breakers: { claim: "closed" }, recent };
}

describe("Agent Ops reducer", () => {
  it("a node runs at its start and ends with its status; the shared state follows the run", () => {
    let state = reduce(EMPTY, { type: "hello", payload: snapshot() });
    state = reduce(state, {
      type: "events",
      payload: { events: [event({ seq: 1, as_of_seq: 630, as_of_display: "63'" })] },
    });
    expect(state.nodes[nodeKey("claim", "ClaimIntake")]?.status).toBe("running");
    expect(state.shared).toEqual({ seq: 630, minute: "63'", runId: "m-c0001" });
    state = reduce(state, {
      type: "events",
      payload: {
        events: [
          event({ seq: 2, phase: "end", status: "ok", handoff: "ClaimContext", duration_ms: 3 }),
        ],
      },
    });
    expect(state.nodes[nodeKey("claim", "ClaimIntake")]).toMatchObject({
      status: "ok",
      durationMs: 3,
    });
    expect(state.log.at(-1)).toMatchObject({
      key: "agents.logHandoff",
      vars: { agent: "ClaimIntake", handoff: "ClaimContext" },
    });
  });

  it("an event already in the snapshot is not applied twice; a running node in hello ended", () => {
    const hello = reduce(EMPTY, { type: "hello", payload: snapshot([event({ seq: 5 })]) });
    expect(hello.nodes[nodeKey("claim", "ClaimIntake")]?.status).toBe("ok");
    const again = reduce(hello, { type: "events", payload: { events: [event({ seq: 5 })] } });
    expect(again.nodes[nodeKey("claim", "ClaimIntake")]?.status).toBe("ok");
  });

  it("the log says what the guardrail blocked, a fallback, a breaker and a cue", () => {
    expect(
      logLine(
        event({
          phase: "end",
          agent: "Guardrail",
          kind: "guardrail",
          status: "rejected",
          guardrail: ["WRONG_COMPARISON"],
        }),
      ),
    ).toMatchObject({
      key: "agents.logRejected",
      vars: { codes: "WRONG_COMPARISON" },
      tone: "warn",
    });
    expect(
      logLine(
        event({
          phase: "end",
          agent: "CircuitBreaker",
          kind: "fallback",
          name: "open",
          breaker: "open",
        }),
      ),
    ).toMatchObject({ key: "agents.logBreaker", tone: "warn" });
    expect(logLine(event({ phase: "end", workflow: "narrator", agent: "Narrator" }))?.key).toBe(
      "agents.logCue",
    );
    expect(logLine(event({ phase: "start" }))).toBeNull();
  });

  it("the log is bounded and counts what the server left out", () => {
    const events = Array.from({ length: LOG_LINES + 20 }, (_, n) =>
      event({ seq: n + 1, phase: "end", status: "ok", handoff: "ClaimContext" }),
    );
    const state = reduce(EMPTY, { type: "events", payload: { events, dropped: 7 } });
    expect(state.log).toHaveLength(LOG_LINES);
    expect(state.dropped).toBe(7);
  });
});

describe("the store and a progressive claim", () => {
  it("rev.2 replaces rev.1; a late rev.1 never replaces rev.2", () => {
    const store = createMatchStore();
    const message = (payload: ClaimResult, n: number) =>
      ({ v: 1, type: "claim", n, match_id: "m", payload }) as const;
    store.getState().apply([message(claim({ revision: 1, model_pass: "queued" }), 1)]);
    store.getState().apply([message(claim({ revision: 1, model_pass: "running" }), 2)]);
    const rev2 = claim({
      revision: 2,
      model_pass: "done",
      rev1_verdict: "TRUE",
      explanation: "model",
    });
    store.getState().apply([message(rev2, 3)]);
    store.getState().apply([message(claim({ revision: 1, model_pass: "running" }), 4)]);
    expect(store.getState().claims.byId["m-c0001"]?.explanation).toBe("model");
    expect(store.getState().claims.order).toEqual(["m-c0001"]);
  });
});
