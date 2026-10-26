// Day 6 landing page (docs/day6-design.md sections 6.5 and 8.5): "Start my demo match" makes a
// private session and opens the studio; refusals say when to try again; "my matches" forgets a
// session the server removed; the three steps open one demo match; the MCP address; the project
// links; every scenario under "Advanced"; the private chip shows on a private session only.
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LandingPage from "@/app/page";
import { PrivateChip } from "@/components/common/PrivateChip";
import { MY_SESSIONS_KEY, mySessions } from "@/lib/sessions";
import { renderWith } from "./render";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const MINE = "dominant_loser-42~k3v9x2ma";
const GONE = "comeback_var-7~zzzzzzzz";

function info(id: string, extra: Record<string, unknown> = {}) {
  return {
    match_id: id,
    scenario: "dominant_loser",
    seed: 42,
    status: "created",
    error: null,
    speed: 20,
    from_minute: 0,
    events_total: 2000,
    events_applied: 0,
    home: { id: "harbor_lions", name: "Harbor Lions FC", short_name: "HAR" },
    away: { id: "kestrel_athletic", name: "Kestrel Athletic", short_name: "KES" },
    moments: 0,
    claims: 0,
    synthetic: true,
    disclaimer: "Synthetic data",
    ...extra,
  };
}

type Answer = { status: number; body: unknown };

const MCP = { enabled: true, status: "ok", path: "/mcp", transport: "streamable-http" };
let started: string[] = [];

function serve(post: Answer, health: Record<string, unknown> = { mcp: MCP }) {
  const posted: unknown[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
    if (init?.method === "POST" && url.pathname === "/matches") {
      posted.push(JSON.parse(String(init.body)));
      return json(post.status, post.body);
    }
    if (init?.method === "POST" && url.pathname === `/matches/${MINE}/start`) {
      started.push(MINE);
      return json(200, info(MINE, { private: true, status: "running" }));
    }
    if (url.pathname === "/healthz") {
      return json(200, { status: "ok", llm_provider: "mock", model: "mock", degraded: [],
                         sessions: 0, synthetic: true, ...health }); // prettier-ignore
    }
    if (url.pathname === "/matches") return json(200, []);
    if (url.pathname === "/scenarios") return json(200, []);
    if (url.pathname === `/matches/${MINE}`)
      return json(200, { info: info(MINE, { private: true }) });
    return json(404, { detail: "unknown match" });
  });
  vi.stubGlobal("fetch", impl);
  return posted;
}

beforeEach(() => {
  push.mockReset();
  started = [];
  window.localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe("landing page", () => {
  it("starts a private demo match and opens its studio", async () => {
    const posted = serve({ status: 200, body: info(MINE, { private: true }) });
    renderWith(<LandingPage />);
    await act(async () => fireEvent.click(await screen.findByTestId("start-my-demo")));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/studio/?match=${MINE}`));
    expect(posted[0]).toMatchObject({ scenario: "dominant_loser", seed: 42, private: true });
    expect(mySessions().map((s) => s.id)).toEqual([MINE]);
  });

  it.each([
    [503, { detail: "demo_full", retry_after_s: 60 }, "The demo is full"],
    [429, { detail: "too_many_sessions", retry_after_s: 120 }, "Try again in 120 s"],
  ])("a %s refusal says when to try again", async (status, body, words) => {
    serve({ status, body });
    renderWith(<LandingPage />);
    await act(async () => fireEvent.click(await screen.findByTestId("start-my-demo")));
    expect(await screen.findByText(new RegExp(words))).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("lists my matches and forgets one the server removed", async () => {
    window.localStorage.setItem(
      MY_SESSIONS_KEY,
      JSON.stringify([
        { id: GONE, created: 2 },
        { id: MINE, created: 1 },
      ]),
    );
    serve({ status: 200, body: info(MINE) });
    renderWith(<LandingPage />);
    const mine = await screen.findByTestId("my-sessions");
    expect(mine).toHaveTextContent(MINE);
    expect(mine).not.toHaveTextContent(GONE);
    await waitFor(() => expect(mySessions().map((s) => s.id)).toEqual([MINE]));
  });

  it("the steps open one demo match: made by the first, started for the fan by the second", async () => {
    const posted = serve({ status: 200, body: info(MINE, { private: true }) });
    renderWith(<LandingPage />);
    await act(async () => fireEvent.click(await screen.findByTestId("step-open-studio")));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/studio/?match=${MINE}`));
    expect(posted).toEqual([expect.objectContaining({ private: true, autostart: false })]);
    expect(started).toEqual([]); // the studio has its own play button

    await act(async () => fireEvent.click(screen.getByTestId("step-open-fan")));
    await waitFor(() => expect(push).toHaveBeenLastCalledWith(`/fan/?match=${MINE}`));
    expect(posted).toHaveLength(1); // the same match, not another one
    expect(started).toEqual([MINE]); // the fan player has no play button

    await act(async () => fireEvent.click(screen.getByTestId("step-open-overlay")));
    await waitFor(() =>
      expect(push).toHaveBeenLastCalledWith(`/overlay/?match=${MINE}&lang=en&debug=1`),
    );
    expect(started).toEqual([MINE]); // already running
  });

  it("a step with no demo match yet makes one that plays at once for the fan", async () => {
    const posted = serve({ status: 200, body: info(MINE, { private: true, status: "running" }) });
    renderWith(<LandingPage />);
    await act(async () => fireEvent.click(await screen.findByTestId("step-open-fan")));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/fan/?match=${MINE}`));
    expect(posted).toEqual([expect.objectContaining({ private: true, autostart: true })]);
  });

  it("step 3 shows this server's MCP address, ready to copy", async () => {
    serve({ status: 200, body: info(MINE) });
    const writeText = vi.fn(async () => {});
    Object.defineProperty(window.navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    renderWith(<LandingPage />);
    const address = await screen.findByTestId("mcp-url");
    const url = `${window.location.origin}/mcp`;
    expect(address).toHaveTextContent(url);
    await act(async () => fireEvent.click(within(address).getByRole("button")));
    expect(writeText).toHaveBeenCalledWith(url);
    expect(address).toHaveTextContent("Copied");
  });

  it("without MCP, step 3 says so", async () => {
    serve({ status: 200, body: info(MINE) }, { mcp: null });
    renderWith(<LandingPage />);
    expect(await screen.findByTestId("mcp-off")).toHaveTextContent("MCP is turned off");
    expect(screen.queryByTestId("mcp-url")).toBeNull();
  });

  it("what to try, the project links and every scenario under Advanced", async () => {
    serve({ status: 200, body: info(MINE) });
    renderWith(<LandingPage />);
    expect(await screen.findByTestId("intro")).toHaveTextContent("Vietnamese commentary");
    // Day 7: Agent Ops and Chaos are on the list, with a way into Agent Ops
    expect(screen.getByTestId("what-to-try").querySelectorAll("li")).toHaveLength(7);
    expect(screen.getByTestId("what-to-try")).toHaveTextContent("Chaos");
    expect(screen.getByTestId("open-agents")).toHaveTextContent("Agent Ops");
    // no address at build time: no dead link, the words say it comes later
    const links = screen.getByTestId("project-links");
    expect(links).toHaveTextContent("Source code: coming soon");
    expect(links).toHaveTextContent("Demo video: coming soon");
    expect(links.querySelector("a")).toBeNull();
    const advanced = screen.getByTestId("advanced");
    expect(advanced).not.toHaveAttribute("open");
    expect(advanced).toHaveTextContent("Choose a match");
  });

  it("is in Vietnamese for a viewer who chose it", async () => {
    serve({ status: 200, body: info(MINE) });
    renderWith(<LandingPage />, "vi");
    expect(await screen.findByTestId("intro")).toHaveTextContent("bình luận tiếng Việt");
    expect(screen.getByTestId("step-open-studio")).toHaveTextContent("Mở Studio");
  });
});

describe("private chip", () => {
  it("shows on a private session only", () => {
    const { rerender } = renderWith(<PrivateChip matchId={MINE} />);
    expect(screen.getByTestId("private-chip")).toHaveTextContent("Your private session");
    rerender(<PrivateChip matchId="dominant_loser-42" />);
    expect(screen.queryByTestId("private-chip")).toBeNull();
    rerender(<PrivateChip matchId={null} />);
    expect(screen.queryByTestId("private-chip")).toBeNull();
  });
});
