// Day 7 components (docs/day7-design.md sections 2.4-2.6): the verdict card of a progressive
// claim, the Agent Ops lanes (state in an icon and words), the Chaos panel.
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChaosPanel } from "@/components/agents/ChaosPanel";
import { Lanes } from "@/components/agents/Lanes";
import { VerdictCard } from "@/components/studio/ClaimBox";
import { EMPTY, nodeKey, type AgentOpsState } from "@/lib/agent-ops";
import type { ChaosStatus, ClaimResult } from "@/lib/contracts";
import { fakeFetch, renderWith } from "./render";

afterEach(() => vi.unstubAllGlobals());

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
    explanation: "Trong hiệp 2, HAR kiểm soát bóng 61%.",
    explanation_source: "template",
    analyst_source: "rule",
    model: "qwen2.5:1.5b",
    evidence_event_ids: [],
    guardrail: { passed: true, violations: [] },
    degraded: [],
    timings_ms: { total: 310 },
    trace: null,
    ...extra,
  };
}

describe("VerdictCard of a progressive claim", () => {
  it("rev.1: the rules' time and a status line while the model checks", () => {
    renderWith(
      <VerdictCard claim={claim({ revision: 1, model_pass: "running" })} onDetails={() => {}} />,
    );
    expect(screen.getAllByTestId("claim-source")[0]).toHaveTextContent("Rules · 0.3 s");
    expect(screen.getByRole("status")).toHaveTextContent("The model is checking…");
    expect(screen.getByTestId("verdict-card")).toHaveAttribute("data-model-pass", "running");
  });

  it("rev.2: the model's chip, its reading, and a verdict it changed, in Vietnamese", () => {
    renderWith(
      <VerdictCard
        claim={claim({
          revision: 2,
          model_pass: "done",
          rev1_verdict: "INSUFFICIENT_DATA",
          analyst_source: "llm",
          explanation_source: "llm",
          timings_ms: { total: 20000, model_pass: 21000 },
        })}
        onDetails={() => {}}
      />,
      "vi",
    );
    expect(screen.getAllByTestId("claim-source")[0]).toHaveTextContent(
      "Model · qwen2.5:1.5b · trực tiếp · 21.0 s",
    );
    const notes = screen.getAllByTestId("claim-note").map((n) => n.textContent);
    expect(notes).toContain("Model đọc giống luật");
    expect(notes.at(-1)).toMatch(/^Cập nhật sau khi model phân tích: .+ → Đúng$/);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("a skipped pass says why, and never shows a raw source name", () => {
    renderWith(
      <VerdictCard
        claim={claim({
          revision: 2,
          model_pass: "skipped",
          skip_reason: "busy",
          rev1_verdict: "TRUE",
        })}
        onDetails={() => {}}
      />,
    );
    const card = screen.getByTestId("verdict-card");
    expect(card).toHaveTextContent("Model busy: the rules' answer stands");
    expect(card.textContent).not.toMatch(/\btemplate\b|\bmock\b/);
  });
});

describe("Agent Ops lanes", () => {
  it("every node says its state in words and an icon; an edge names the typed message", () => {
    const state: AgentOpsState = {
      ...EMPTY,
      nodes: {
        [nodeKey("claim", "Guardrail")]: {
          status: "rejected",
          runId: "c",
          durationMs: 4,
          source: null,
        },
        [nodeKey("claim", "Explainer")]: {
          status: "running",
          runId: "c",
          durationMs: null,
          source: null,
        },
      },
    };
    const picked: string[] = [];
    renderWith(<Lanes state={state} selected={null} onSelect={(k) => picked.push(k)} />);
    const guard = screen
      .getAllByTestId("agent-node")
      .find((n) => n.dataset.agent === "Guardrail" && n.dataset.workflow === "claim");
    expect(guard).toHaveTextContent("blocked by the guardrail · 4 ms");
    const explainer = screen
      .getAllByTestId("agent-node")
      .find((n) => n.dataset.agent === "Explainer");
    expect(explainer).toHaveTextContent("running");
    expect(
      screen.getAllByTestId("agent-node").find((n) => n.dataset.agent === "Judge"),
    ).toHaveTextContent("idle");
    expect(screen.getAllByTestId("agent-edge").map((e) => e.textContent)).toContain(
      "→ ClaimContext",
    );
    fireEvent.click(guard!);
    expect(picked).toEqual([nodeKey("claim", "Guardrail")]);
  });
});

describe("ChaosPanel", () => {
  const status: ChaosStatus = { available: true, fault: null, lies_told: 0, lies_published: 0 };

  it("is locked on a shared match, with a way to start one's own", () => {
    const started: string[] = [];
    renderWith(
      <ChaosPanel
        matchId="m"
        chaos={{ available: false }}
        studioTemplates={false}
        onChanged={() => {}}
        onStartDemo={() => started.push("demo")}
      />,
    );
    expect(screen.getByTestId("chaos-locked")).toHaveTextContent("start your own demo match");
    fireEvent.click(screen.getByRole("button"));
    expect(started).toEqual(["demo"]);
  });

  it("switches a fault on and resets it, and shows the wrong numbers that reached a screen", async () => {
    const fetch = fakeFetch({ "/matches/m/chaos": { body: { ...status, fault: "model_lies" } } });
    vi.stubGlobal("fetch", fetch.impl);
    const changed: number[] = [];
    renderWith(
      <ChaosPanel
        matchId="m"
        chaos={{ ...status, lies_told: 12, lies_published: 0 }}
        studioTemplates
        onChanged={() => changed.push(1)}
        onStartDemo={() => {}}
      />,
    );
    expect(screen.getByTestId("chaos-state")).toHaveTextContent("Nothing is broken.");
    expect(screen.getByTestId("chaos-lies")).toHaveTextContent(
      "Wrong numbers that reached a screen: 0 (the model lied 12 times)",
    );
    fireEvent.click(screen.getByTestId("chaos-model_lies"));
    await waitFor(() => expect(changed).toHaveLength(1));
    const body = JSON.parse(String(fetch.calls.at(-1)?.init?.body));
    expect(body).toEqual({ fault: "model_lies", duration_s: 300 });
    fireEvent.click(screen.getByTestId("chaos-reset"));
    await waitFor(() => expect(changed).toHaveLength(2));
    expect(JSON.parse(String(fetch.calls.at(-1)?.init?.body)).fault).toBeNull();
    expect(screen.getByText(/only claims meet the fault/)).toBeInTheDocument();
  });
});
