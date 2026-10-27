// Day 6 states of the studio panels (docs/day6-design.md section 8.4): a panel whose data did
// not come says so and offers a retry (on a finished match nothing else would load it again);
// with no data yet it says what it waits for.
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ItemDrawer } from "@/components/studio/ItemDrawer";
import { MomentumTimeline } from "@/components/studio/MomentumTimeline";
import { StatsPanel } from "@/components/studio/StatsPanel";
import { TalkingPointQueue } from "@/components/studio/TalkingPointQueue";
import { createMatchStore } from "@/store/match-store";
import { renderWith } from "./render";

const MATCH = "dominant_loser-42";
const TEAMS = { home: "harbor_lions", away: "kestrel_athletic" };
const TIMELINE = {
  as_of_seq: 900,
  ...TEAMS,
  half_time_minute: null,
  markers: [],
  window_minutes: 5,
  points: [
    { minute: 5, value: 0.2 },
    { minute: 10, value: -0.1 },
  ],
};
const STATS = {
  as_of_seq: 900,
  period: "FULL",
  ...TEAMS,
  rows: [
    {
      metric: "shots",
      label_en: "Shots",
      label_vi: "Cú sút",
      home: { value: 4, display: "4" },
      away: { value: 1, display: "1" },
    },
  ],
};

/** A fetch whose answer to `path` is a 500 until `heal()`, then `body`. */
function flaky(path: string, body: unknown) {
  let healed = false;
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes(path) && healed) return new Response(JSON.stringify(body));
      return new Response(JSON.stringify({ detail: "boom" }), { status: 500 });
    }),
  );
  return { heal: () => (healed = true), calls };
}

afterEach(() => vi.unstubAllGlobals());

describe("panel states", () => {
  it("momentum: a failed load says so, and the retry draws the chart", async () => {
    const server = flaky("/timeline", TIMELINE);
    renderWith(<MomentumTimeline matchId={MATCH} version={1} scenario={null} />);
    expect(await screen.findByText("This could not be loaded.")).toBeInTheDocument();
    server.heal();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
    expect(await screen.findByTestId("momentum-timeline")).toBeInTheDocument();
  });

  it("momentum and stats with nothing to show yet: not enough data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/timeline")
          ? new Response(JSON.stringify({ ...TIMELINE, points: [] }))
          : new Response(JSON.stringify({ ...STATS, rows: [] })),
      ),
    );
    renderWith(
      <>
        <MomentumTimeline matchId={MATCH} version={1} scenario={null} />
        <StatsPanel matchId={MATCH} version={1} scenario={null} />
      </>,
    );
    await waitFor(() => expect(screen.getAllByText(/Not enough data yet/)).toHaveLength(2));
  });

  it("stats: a failed load says so, and the retry shows the rows", async () => {
    const server = flaky("/stats", STATS);
    renderWith(<StatsPanel matchId={MATCH} version={1} scenario={null} />, "vi");
    expect(await screen.findByText("Không tải được.")).toBeInTheDocument();
    server.heal();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Thử lại" })));
    expect(await screen.findByTestId("stats-row")).toHaveAttribute("data-metric", "shots");
  });

  it("the drawer: a failed load offers a retry", async () => {
    const server = flaky("/claims/", { detail: "never used" });
    renderWith(
      <ItemDrawer
        matchId={MATCH}
        target={{ kind: "claim", id: `${MATCH}-c0001` }}
        onClose={() => {}}
        homeId={TEAMS.home}
        scenario={null}
        players={new Map()}
      />,
    );
    expect(await screen.findByTestId("load-failed")).toBeInTheDocument();
    const before = server.calls.length;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
    await waitFor(() => expect(server.calls.length).toBeGreaterThan(before));
  });

  it("an empty talking point queue says when suggestions come", () => {
    renderWith(
      <TalkingPointQueue
        matchId={MATCH}
        points={[]}
        pending={{}}
        store={createMatchStore()}
        onTrace={() => {}}
      />,
    );
    const onlyItems = () =>
      [...screen.getByTestId("talking-points").children].every((child) => child.tagName === "LI");
    expect(screen.getByText(/come with the key moments/)).toBeInTheDocument();
    expect(onlyItems()).toBe(true); // axe `list` (M10, on the Space)
    fireEvent.click(screen.getByTestId("tp-tab-dismissed"));
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
    expect(onlyItems()).toBe(true);
  });
});
