import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionBanner } from "@/components/common/ConnectionBanner";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { CommentaryCard } from "@/components/fan/CommentaryCard";
import { CommentaryFeed, PlayerTab, RecapPanel } from "@/components/fan/FanFeed";
import { ProfileSetup } from "@/components/fan/ProfileSetup";
import { LowerThird } from "@/components/overlay/LowerThird";
import { useAnimatedItem } from "@/components/overlay/Stage";
import type { CommentaryView, LowerThirdView, ScenarioInfo } from "@/lib/contracts";
import { DEFAULT_PROFILE } from "@/lib/profile";
import { applyBatch, initialState, items } from "@/store/match-store";
import { fixture, ofType } from "../fixtures";
import { fakeFetch, renderWith } from "./render";

const analyst = fixture("dominant_loser-42.analyst-en");
const casual = fixture("dominant_loser-42.casual-vi-beginner-har");
const longAnalyst = fixture("dominant_loser-42.llm-long.analyst-en");
const longCasual = fixture("dominant_loser-42.llm-long.casual-vi-beginner-har");
const lowerThirds = ofType(analyst, "lower_third").map((m) => m.payload);

const scenario: ScenarioInfo = {
  key: "dominant_loser",
  title_vi: "t",
  title_en: "t",
  home: {
    id: "harbor_lions",
    name: "Harbor Lions FC",
    short_name: "HAR",
    primary_color: "#0B3D91",
    secondary_color: "#F2C230",
  },
  away: {
    id: "kestrel_athletic",
    name: "Kestrel Athletic",
    short_name: "KES",
    primary_color: "#8C1D40",
    secondary_color: "#FFFFFF",
  },
  default_seed: 42,
  example_claims: { vi: [], en: [] },
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("LowerThird", () => {
  it("every template renders the server's words and numbers with the synthetic tag", () => {
    const templates = new Set(lowerThirds.map((lt) => lt.template));
    expect(templates).toEqual(new Set(["score_update", "team_compare", "player_card", "momentum"]));
    for (const item of lowerThirds) {
      const { unmount } = render(<LowerThird item={item} />);
      const box = screen.getByTestId("lower-third");
      expect(box.dataset.template).toBe(item.template);
      expect(box).toHaveTextContent(item.title);
      expect(box).toHaveTextContent(item.match_clock);
      for (const stat of item.stats) {
        for (const shown of [stat.value, stat.home, stat.away]) {
          if (shown) expect(box).toHaveTextContent(shown);
        }
      }
      expect(within(box).getByTestId("lt-synthetic")).toHaveTextContent("Synthetic data");
      unmount();
    }
  });

  it("long model texts never break the layout (fixture B): titles stay on one line", () => {
    const long = ofType(longAnalyst, "lower_third").map((m) => m.payload);
    expect(long.length).toBeGreaterThan(5);
    for (const item of long) {
      const { unmount } = render(<LowerThird item={item} />);
      const title = screen.getByText(item.title);
      expect(title.className).toContain("truncate");
      unmount();
    }
  });
});

describe("useAnimatedItem", () => {
  function Probe({ item }: { item: LowerThirdView | null }) {
    const shown = useAnimatedItem(item);
    return <span data-testid="probe">{`${shown.item?.id ?? "none"}:${shown.phase}`}</span>;
  }

  it("the old item exits before the new one enters", () => {
    vi.useFakeTimers();
    const [a, b] = lowerThirds;
    const view = render(<Probe item={a!} />);
    expect(screen.getByTestId("probe")).toHaveTextContent(`${a!.id}:enter`);
    view.rerender(<Probe item={b!} />);
    expect(screen.getByTestId("probe")).toHaveTextContent(`${a!.id}:exit`);
    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByTestId("probe")).toHaveTextContent(`${b!.id}:enter`);
    view.rerender(<Probe item={null} />);
    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByTestId("probe")).toHaveTextContent("none:static");
  });
});

describe("fan player", () => {
  const casualItems = items(applyBatch(initialState, casual).commentary);

  it("cards show club framing, emphasis and rule explanations as the server sent them", () => {
    const framed = casualItems.find((c) => c.framing);
    const explained = casualItems.find((c) => c.explanation);
    expect(framed && explained).toBeTruthy();
    renderWith(<CommentaryCard item={framed!} scenario={scenario} ownTeam="harbor_lions" />, "vi");
    const card = screen.getByTestId("commentary-card");
    expect(card).toHaveTextContent(framed!.framing!);
    expect(card.dataset.emphasis).toBe(framed!.emphasis);
    renderWith(
      <CommentaryCard item={explained!} scenario={scenario} ownTeam="harbor_lions" />,
      "vi",
    );
    expect(screen.getByTestId("rule-explanation")).toHaveTextContent(explained!.explanation!.title);
  });

  it("the feed shows every long model text in full, newest first (fixture B)", () => {
    for (const stream of [longAnalyst, longCasual]) {
      const views = ofType(stream, "commentary").map((m) => m.payload) as CommentaryView[];
      const { unmount } = renderWith(
        <CommentaryFeed items={views} scenario={scenario} ownTeam={null} waiting={false} />,
      );
      const cards = screen.getAllByTestId("commentary-card");
      expect(cards).toHaveLength(views.length);
      expect(cards[0]?.dataset.id).toBe(views[views.length - 1]?.id);
      for (const view of views) expect(screen.getAllByText(view.text).length).toBeGreaterThan(0);
      unmount();
    }
  });

  it("recap and player tabs", () => {
    const state = applyBatch(initialState, casual);
    renderWith(<RecapPanel recaps={state.recaps} />, "vi");
    expect(screen.getByTestId("recap-panel")).toHaveTextContent(state.recaps.FULL!.title);
    renderWith(<RecapPanel recaps={{}} />, "vi");
    expect(screen.getAllByTestId("recap-panel")[1]).toHaveTextContent(
      "Tóm tắt hiệp 1 có khi nghỉ giữa hiệp.",
    );
    renderWith(<PlayerTab items={casualItems} playerName={null} />, "vi");
    expect(screen.getByText("Chọn một cầu thủ trong hồ sơ để theo dõi.")).toBeInTheDocument();
  });

  it("profile setup returns the chosen profile", async () => {
    vi.stubGlobal(
      "fetch",
      fakeFetch({
        "/commentary?": { body: items(applyBatch(initialState, casual).commentary) },
        "/stats?": {
          body: {
            as_of_seq: 1,
            period: "FULL",
            home: "harbor_lions",
            away: "kestrel_athletic",
            rows: [],
          },
        },
      }).impl,
    );
    const done = vi.fn();
    renderWith(
      <ProfileSetup
        matchId="dominant_loser-42"
        initial={DEFAULT_PROFILE}
        scenario={scenario}
        players={new Map()}
        onDone={done}
      />,
      "vi",
    );
    fireEvent.click(screen.getByTestId("mode-analyst"));
    fireEvent.click(screen.getByText("English"));
    fireEvent.click(screen.getByTestId("club-kestrel_athletic"));
    fireEvent.click(screen.getByTestId("beginner-setup"));
    fireEvent.click(screen.getByTestId("profile-start"));
    expect(done).toHaveBeenCalledWith({
      mode: "analyst",
      language: "en",
      team: "kestrel_athletic",
      player: null,
      beginner: true,
      metric: null,
    });
    expect(await screen.findAllByText(/^Ví dụ:/)).not.toHaveLength(0);
  });
});

describe("common", () => {
  it("a failing panel shows its fallback, not a blank page", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const Boom = () => {
      throw new Error("boom");
    };
    render(
      <div>
        <ErrorBoundary fallback={<p>fallback</p>}>
          <Boom />
        </ErrorBoundary>
        <p>rest of the page</p>
      </div>,
    );
    expect(screen.getByText("fallback")).toBeInTheDocument();
    expect(screen.getByText("rest of the page")).toBeInTheDocument();
    spy.mockRestore();
    warn.mockRestore();
  });

  it("the connection banner says what is happening", () => {
    const create = vi.fn();
    const { rerender } = renderWith(<ConnectionBanner state={{ status: "open" }} />);
    expect(screen.queryByTestId("connection-banner")).toBeNull();
    rerender(<ConnectionBanner state={{ status: "fatal", code: 4404 }} onCreate={create} />);
    fireEvent.click(screen.getByText("Create this match"));
    expect(create).toHaveBeenCalled();
    rerender(
      <ConnectionBanner
        state={{ status: "reconnecting", code: 1006, retryInMs: 3000, retryAt: Date.now() + 3000 }}
      />,
    );
    expect(screen.getByTestId("connection-banner")).toHaveTextContent("Reconnecting in 3s");
    rerender(<ConnectionBanner state={{ status: "fatal", code: 4422 }} />);
    expect(screen.getByTestId("connection-banner")).toHaveTextContent("not valid for this match");
  });
});
