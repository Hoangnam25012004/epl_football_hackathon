"""Oracle: every number of get_stat / compare_stat is the registry's own (docs/day6-design.md
sections 10.2 and 10.3). The expected values are computed here on views built apart from the
MCP source, with windows built by hand, so a shortcut in the tools cannot hide."""

import pytest
from helpers import generated, run, source
from hypothesis import given
from hypothesis import strategies as st
from mcp_helpers import DL, MATCHES, static

from pundit.domain.ids import team_of_player
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, H2, MinuteWindow, RollingWindow
from pundit.mcp_server import tools
from pundit.mcp_server.outputs import MAX_EVIDENCE, StatValue
from pundit.mcp_server.params import WindowArg
from pundit.orchestration.evaluation import seq_at
from pundit.stats import REGISTRY, MetricParams, StatResult, compare, compute
from pundit.stats.result import display

# a striker of each team: players with shots, passes and runs in every scenario
STRIKERS = {
    "harbor_lions": "harbor_lions-09",
    "kestrel_athletic": "kestrel_athletic-11",
    "ironvale_united": "ironvale_united-09",
    "saltmarsh_rovers": "saltmarsh_rovers-09",
}
TEAM_METRICS = sorted(name for name, spec in REGISTRY.items() if spec.scope == "team")


def view_of(match_id: str, at: str | None) -> LogView:
    scenario, seed = match_id.rsplit("-", 1)
    src = source(scenario, int(seed))
    events = generated(scenario, int(seed)).events
    if at == "HT":
        seq: int | None = next(e.seq for e in events if e.type == "period_end" and e.period == 1)
    else:
        seq = seq_at(events, at)
    return src.view_at(src.latest_seq() if seq is None else seq)


def windows(view: LogView) -> list[tuple[WindowArg | None, MinuteWindow | RollingWindow]]:
    last = view.last
    assert last is not None
    return [
        (None, FULL),
        (WindowArg(type="second_half"), H2),
        (WindowArg(type="last_minutes", minutes=15), RollingWindow.last(15, last.match_ms)),
    ]


def same(got: StatValue, want: StatResult) -> bool:
    return (
        got.metric, got.team, got.player, got.window, got.value, got.display, got.unit,
        got.numerator, got.denominator, got.status, got.evidence_event_ids, got.evidence_total,
        got.definition,
    ) == (
        want.metric, want.team, want.player, want.window.label(), want.value, display(want),
        want.unit, want.numerator, want.denominator, want.status,
        list(want.evidence_event_ids[:MAX_EVIDENCE]), len(want.evidence_event_ids),
        want.definition,
    )  # fmt: skip


async def sweep(match_id: str, at: str | None = None) -> tuple[int, list[str]]:
    """Every metric x team (and a striker of each team) x 3 windows; the number of checks and
    the ones that differ."""
    view = view_of(match_id, at)
    teams = view.meta.team_ids
    checked, wrong = 0, []
    for name, spec in sorted(REGISTRY.items()):
        for arg, window in windows(view):
            queries: list[tuple[str | None, str | None]] = []
            if spec.scope == "match":
                queries.append((None, None))
            elif spec.scope == "player":
                queries += [(None, STRIKERS[team]) for team in teams]
            else:
                queries += [(team, None) for team in teams]
                if spec.supports_player:
                    queries += [(None, STRIKERS[team]) for team in teams]
            for team, player in queries:
                want_team = team
                if spec.scope == "team" and team is None and player is not None:
                    want_team = team_of_player(player)
                want = compute(
                    view, name, MetricParams(team=want_team, player=player, window=window)
                )
                got = await tools.get_stat(
                    static(), match_id=match_id, metric=name, team=team, player=player,
                    window=arg, as_of=at,
                )  # fmt: skip
                checked += 1
                if not same(got.stat, want) or got.as_of.seq != view.last_seq:
                    wrong.append(f"get_stat {match_id} {name} {team or player} {window.label()}")
            if spec.scope != "team":
                continue
            expected = compare(view, name, window)
            pair = await tools.compare_stat(
                static(), match_id=match_id, metric=name, window=arg, as_of=at
            )
            checked += 1
            if not (
                same(pair.home, expected.home)
                and same(pair.away, expected.away)
                and (pair.diff, pair.ratio) == (expected.diff, expected.ratio)
            ):
                wrong.append(f"compare_stat {match_id} {name} {window.label()}")
    return checked, wrong


@pytest.mark.parametrize("match_id", MATCHES)
def test_every_metric_is_the_registrys(match_id: str) -> None:
    checked, wrong = run(sweep(match_id))
    assert wrong == []
    assert checked > 250


@pytest.mark.parametrize("at", ["HT", "63'"])
def test_a_pinned_minute_is_the_registrys(at: str) -> None:
    checked, wrong = run(sweep(DL, at))
    assert wrong == []
    assert checked > 250


@given(
    metric=st.sampled_from(TEAM_METRICS),
    home=st.booleans(),
    start=st.integers(min_value=1, max_value=85),
    length=st.integers(min_value=1, max_value=40),
    minute=st.integers(min_value=1, max_value=90),
)
def test_random_windows_and_minutes_are_the_registrys(
    metric: str, home: bool, start: int, length: int, minute: int
) -> None:
    at = f"{minute}'"
    view = view_of(DL, at)
    team = view.meta.team_ids[0 if home else 1]
    window = WindowArg(type="minute_range", start_minute=start, end_minute=start + length)
    want = compute(
        view,
        metric,
        MetricParams(team=team, window=MinuteWindow(start_min=start, end_min=start + length)),
    )
    got = run(
        tools.get_stat(static(), match_id=DL, metric=metric, team=team, window=window, as_of=at)
    )
    assert same(got.stat, want)


@pytest.mark.parametrize(
    "spelling",
    ["kestrel_athletic", "KES", "kes", "Kestrel", "Kestrel Athletic FC", "away", "Đội khách"],
)
def test_a_team_spelt_another_way_gives_the_same_answer(spelling: str) -> None:
    window = WindowArg(type="second_half")
    base = run(
        tools.get_stat(static(), match_id=DL, metric="xg", team="kestrel_athletic", window=window)
    )
    other = run(tools.get_stat(static(), match_id=DL, metric="xg", team=spelling, window=window))
    assert other == base


@pytest.mark.parametrize(
    ("canonical", "spelling"),
    [
        ("kestrel_athletic-11", "Jordan Ellery"),
        ("kestrel_athletic-11", "JORDAN ELLERY"),
        ("kestrel_athletic-11", "J. Ellery"),
        ("kestrel_athletic-11", "Ellery"),
        ("harbor_lions-06", "Phạm Gia Bảo"),
        ("harbor_lions-06", "Pham Gia Bao"),
        ("harbor_lions-06", "Gia Bảo"),
    ],
)
def test_a_player_spelt_another_way_gives_the_same_answer(canonical: str, spelling: str) -> None:
    base = run(tools.get_stat(static(), match_id=DL, metric="passes", player=canonical))
    other = run(tools.get_stat(static(), match_id=DL, metric="passes", player=spelling))
    assert other == base
