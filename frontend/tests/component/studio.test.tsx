import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ClaimBox, VerdictCard } from "@/components/studio/ClaimBox";
import { EventFeed } from "@/components/studio/EventFeed";
import { ItemDrawer } from "@/components/studio/ItemDrawer";
import { TalkingPointQueue } from "@/components/studio/TalkingPointQueue";
import { TraceGantt, traceSummary } from "@/components/studio/TraceGantt";
import type { ClaimResult, TraceSpan } from "@/lib/contracts";
import type { MatchEvent } from "@/lib/ws-types";
import { VERDICTS } from "@/lib/verdict";
import { applyBatch, createMatchStore, initialState, items } from "@/store/match-store";
import { fixture, ofType } from "../fixtures";
import { fakeFetch, renderWith } from "./render";

const stream = fixture("dominant_loser-42.analyst-en");
const finalState = applyBatch(initialState, stream);
const points = items(finalState.talkingPoints);
const events = ofType(stream, "event").map((m) => m.payload) as MatchEvent[];

function claim(verdict: ClaimResult["verdict"], extra: Partial<ClaimResult> = {}): ClaimResult {
  return {
    claim_id: `dominant_loser-42-c000${VERDICTS.indexOf(verdict) + 1}`,
    match_id: "dominant_loser-42",
    as_of_seq: 2000,
    as_of_display: "90+6'",
    text: "Đội nhà áp đảo hiệp 2",
    language: "vi",
    verdict,
    reason_code: "ok",
    assertions: [],
    headline: [
      {
        key: "possession:harbor_lions:H2",
        label_vi: "Kiểm soát bóng · HAR · hiệp 2",
        label_en: "Possession · HAR · the second half",
        value: 61,
        unit: "percent",
        display: "61%",
        evidence_event_ids: [],
      },
    ],
    explanation: "Trong hiệp 2, HAR kiểm soát bóng 61%.",
    explanation_source: "template",
    analyst_source: "rule",
    model: "mock",
    evidence_event_ids: [],
    guardrail: { passed: true, violations: [] },
    degraded: [],
    timings_ms: {},
    trace: null,
    ...extra,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("VerdictCard", () => {
  it.each(VERDICTS)("%s has a colour, an icon and words", (verdict) => {
    renderWith(<VerdictCard claim={claim(verdict)} onDetails={() => {}} />);
    const pill = screen.getByTestId("verdict");
    expect(pill.dataset.verdict).toBe(verdict);
    expect(pill.textContent?.length).toBeGreaterThan(2); // icon + label
    expect(pill.getAttribute("style")).toContain("background-color");
    expect(screen.getByTestId("fact")).toHaveTextContent("61%"); // the server's display, verbatim
  });

  it("speaks Vietnamese when the interface does", () => {
    renderWith(<VerdictCard claim={claim("PARTLY_TRUE")} onDetails={() => {}} />, "vi");
    expect(screen.getByTestId("verdict")).toHaveTextContent("Đúng một phần");
    expect(screen.getByTestId("fact")).toHaveTextContent("Kiểm soát bóng · HAR · hiệp 2: 61%");
  });
});

describe("ClaimBox", () => {
  it("sends the claim and shows a 422 under the input, keeping the text", async () => {
    const fetch = fakeFetch({
      "/claims/verify": { status: 422, body: { detail: [{ msg: "text too short" }] } },
    });
    vi.stubGlobal("fetch", fetch.impl);
    renderWith(
      <ClaimBox
        matchId="dominant_loser-42"
        examples={["Đội nhà áp đảo hiệp 2"]}
        history={[]}
        onDetails={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("Đội nhà áp đảo hiệp 2"));
    expect(screen.getByTestId("claim-input")).toHaveValue("Đội nhà áp đảo hiệp 2");
    fireEvent.click(screen.getByTestId("claim-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("text too short");
    expect(screen.getByTestId("claim-input")).toHaveValue("Đội nhà áp đảo hiệp 2");
    const body = JSON.parse(String(fetch.calls[0]?.init?.body));
    expect(body).toEqual({
      match_id: "dominant_loser-42",
      text: "Đội nhà áp đảo hiệp 2",
      language: "auto",
    });
  });

  it("lists the claims of every editor, newest first", () => {
    renderWith(
      <ClaimBox
        matchId="m"
        examples={[]}
        history={[claim("FALSE"), claim("TRUE")]}
        onDetails={() => {}}
      />,
    );
    const cards = screen.getAllByTestId("verdict-card");
    expect(cards.map((c) => within(c).getByTestId("verdict").dataset.verdict)).toEqual([
      "TRUE",
      "FALSE",
    ]);
  });
});

describe("TalkingPointQueue", () => {
  it("marks a decision as sending, then rolls back when the server refuses", async () => {
    const fetch = fakeFetch({ "/status": { status: 500, body: { detail: "boom" } } });
    vi.stubGlobal("fetch", fetch.impl);
    const store = createMatchStore();
    store.getState().apply(stream.slice(0, 1));
    const view = renderWith(
      <TalkingPointQueue
        matchId="dominant_loser-42"
        points={points}
        pending={{}}
        store={store}
        onTrace={() => {}}
      />,
    );
    expect(screen.getAllByTestId("talking-point").length).toBe(points.length);
    const first = screen.getAllByTestId("talking-point")[0]!;
    fireEvent.click(within(first).getByTestId("tp-pin"));
    expect(store.getState().pending.talkingPoints[first.dataset.id!]).toBe("pinned");
    await waitFor(() => expect(store.getState().pending.talkingPoints).toEqual({}));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    view.unmount();
  });

  it("pinned items lead the new tab and every tab counts its items", () => {
    const pinned = { ...points[0]!, status: "pinned" as const };
    const store = createMatchStore();
    renderWith(
      <TalkingPointQueue
        matchId="m"
        points={[...points.slice(1), pinned]}
        pending={{}}
        store={store}
        onTrace={() => {}}
      />,
    );
    expect(screen.getAllByTestId("talking-point")[0]?.dataset.status).toBe("pinned");
    expect(screen.getByTestId("tp-tab-pinned")).toHaveTextContent("(1)");
    fireEvent.click(screen.getByTestId("tp-tab-dismissed"));
    expect(screen.queryAllByTestId("talking-point")).toHaveLength(0);
  });
});

describe("TraceGantt and the drawer", () => {
  const spans: TraceSpan[] = [
    {
      trace_id: "c",
      span_id: "c-s01",
      parent_id: null,
      agent: "ClaimAnalyst",
      kind: "llm",
      name: "analyze",
      input: {},
      output: {},
      status: "retry",
      attempt: 1,
      start_ms: 0,
      duration_ms: 40,
      model: "mock",
      error: null,
    },
    {
      trace_id: "c",
      span_id: "c-s02",
      parent_id: null,
      agent: "EvidenceCollector",
      kind: "tool",
      name: "get_stat",
      input: { metric: "shots" },
      output: { value: 15 },
      status: "ok",
      attempt: 1,
      start_ms: 40,
      duration_ms: 5,
      model: null,
      error: null,
    },
    {
      trace_id: "c",
      span_id: "c-s03",
      parent_id: null,
      agent: "Guardrail",
      kind: "guardrail",
      name: "check",
      input: {},
      output: {},
      status: "rejected",
      attempt: 1,
      start_ms: 45,
      duration_ms: 2,
      model: null,
      error: null,
    },
    {
      trace_id: "c",
      span_id: "c-s04",
      parent_id: null,
      agent: "Explainer",
      kind: "fallback",
      name: "template",
      input: {},
      output: {},
      status: "fallback",
      attempt: 2,
      start_ms: 47,
      duration_ms: 1,
      model: null,
      error: null,
    },
  ];

  it("summarizes the trace and shows a span's input and output", () => {
    expect(traceSummary(spans)).toEqual({ totalMs: 48, llmCalls: 1, rejected: 1, fallback: true });
    renderWith(<TraceGantt spans={spans} />);
    expect(screen.getAllByTestId("trace-row")).toHaveLength(4);
    const tool = screen.getAllByTestId("trace-span").find((s) => s.dataset.kind === "tool")!;
    fireEvent.click(tool);
    expect(screen.getByTestId("trace-detail")).toHaveTextContent('"metric": "shots"');
  });

  it("the drawer loads a claim with its trace, evidence and pitch", async () => {
    const evidence = events.filter((e) => e.type === "shot").slice(0, 3);
    const full = claim("TRUE", {
      evidence_event_ids: evidence.map((e) => e.id),
      trace: spans,
      assertions: [
        {
          assertion: {
            kind: "dominance",
            subject: { kind: "team", team: "harbor_lions" },
            window: { kind: "minute", period: 2 },
            text_span: "Đội nhà áp đảo hiệp 2",
          },
          facts: claim("TRUE").headline,
          outcome: "SUPPORTED",
          rationale_code: "dominance",
        },
      ],
    });
    const fetch = fakeFetch({
      "/claims/": { body: full },
      "/events?ids=": { body: { as_of_seq: 2000, events: evidence, missing: ["x"] } },
    });
    vi.stubGlobal("fetch", fetch.impl);
    renderWith(
      <ItemDrawer
        matchId="dominant_loser-42"
        target={{ kind: "claim", id: full.claim_id }}
        onClose={() => {}}
        homeId="harbor_lions"
        scenario={null}
        players={new Map()}
      />,
    );
    expect(await screen.findByTestId("drawer-why")).toHaveTextContent(
      "Checked against the match data.",
    );
    expect(screen.getByTestId("assertion")).toHaveTextContent("supported");
    fireEvent.click(screen.getByTestId("drawer-tab-evidence"));
    await waitFor(() => expect(screen.getAllByTestId("pitch-point")).toHaveLength(3));
    expect(screen.getByTestId("drawer-evidence")).toHaveTextContent("1 events not available");
    fireEvent.click(screen.getByTestId("drawer-tab-trace"));
    expect(screen.getAllByTestId("trace-span")).toHaveLength(4);
  });
});

describe("EventFeed", () => {
  it("merges the backfill with live events once each, newest first, and filters key events", async () => {
    const backfill = events.slice(0, 60);
    const live = events.slice(40, 120);
    vi.stubGlobal(
      "fetch",
      fakeFetch({ "/events?last=": { body: { as_of_seq: 120, events: backfill, missing: [] } } })
        .impl,
    );
    renderWith(
      <EventFeed matchId="m" live={live} helloCount={1} scenario={null} players={new Map()} />,
    );
    await waitFor(() => expect(screen.getAllByTestId("event-row")).toHaveLength(120));
    const shown = screen.getAllByTestId("event-row");
    expect(shown[0]?.dataset.type).toBe(events[119]?.type);
    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: "Key events" }));
    });
    const key = screen.queryAllByTestId("event-row");
    expect(key.length).toBeLessThan(120);
    expect(
      key.every((row) => row.dataset.type !== "pass" || row.textContent?.includes("Corner")),
    ).toBe(true);
  });
});
