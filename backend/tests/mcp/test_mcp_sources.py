"""Where the MCP tools read a match, and at which minute (docs/day6-design.md 3.2, 3.4)."""

import asyncio

import pytest
from helpers import generated, run
from mcp_helpers import static

from pundit.agents.moments import detect_all
from pundit.api import routes
from pundit.domain.matchtime import format_minute
from pundit.engine.scenarios import SCENARIOS
from pundit.mcp_server import sources
from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.sources import (
    DEFAULT_SEED,
    MAX_SEED,
    StaticMatches,
    check_match_id,
    seq_for,
)
from pundit.orchestration.evaluation import seq_at

EVENTS = generated("dominant_loser", 42).events
HT_SEQ = next(e.seq for e in EVENTS if e.type == "period_end" and e.period == 1)
# a live match at 61': the second half has started and 46' has been fully played
LIVE = [e for e in EVENTS if (e.period, e.clock_ms) < (2, 15 * 60_000 + 30_000)]
NOW = LIVE[-1]


def test_the_default_seed_is_the_apis() -> None:
    assert DEFAULT_SEED == routes.DEFAULT_SEED


@pytest.mark.parametrize(
    "match_id", ["dominant_loser-42", "comeback_var-7", "balanced-0", "dominant_loser-42~ab12cd34"]
)
def test_match_ids(match_id: str) -> None:
    assert check_match_id(f" {match_id} ") == match_id


@pytest.mark.parametrize(
    "match_id",
    ["", "dominant_loser", "Dominant_Loser-42", "dominant loser-42", "dominant_loser-12345678",
     "dominant_loser-42~short", "dominant_loser-42~ABCD1234", "../etc/passwd", "dl-4 2"],
)  # fmt: skip
def test_not_match_ids(match_id: str) -> None:
    with pytest.raises(ToolInputError, match="list_matches"):
        check_match_id(match_id)


@pytest.mark.parametrize("as_of", [None, "", "  ", "now", "NOW", "live"])
def test_no_minute_is_the_latest_event(as_of: str | None) -> None:
    assert seq_for(EVENTS, as_of, complete=True) == EVENTS[-1].seq
    assert seq_for(LIVE, as_of, complete=False) == NOW.seq


def test_full_time_only_when_the_match_is_over() -> None:
    assert seq_for(EVENTS, "FT", complete=True) == EVENTS[-1].seq
    assert seq_for(EVENTS, "ft", complete=True) == EVENTS[-1].seq
    with pytest.raises(ToolInputError, match="the match is not over"):
        seq_for(LIVE, "FT", complete=False)


def test_half_time_is_the_end_of_the_first_half() -> None:
    assert seq_for(EVENTS, "HT", complete=True) == HT_SEQ
    assert seq_for(LIVE, "ht", complete=False) == HT_SEQ
    with pytest.raises(ToolInputError, match="half-time has not been reached"):
        seq_for(EVENTS[:300], "HT", complete=False)


@pytest.mark.parametrize("as_of", ["1'", "27'", "45'", "45+2'", "46'", "63'", "90'", "90+3'"])
def test_a_minute_is_the_last_event_of_that_minute(as_of: str) -> None:
    assert seq_for(EVENTS, as_of, complete=True) == seq_at(EVENTS, as_of)


def test_a_minute_already_played_in_a_live_match() -> None:
    assert seq_for(LIVE, "46'", complete=False) == seq_at(LIVE, "46'")


@pytest.mark.parametrize("ahead", [0, 1, 20])
def test_a_live_match_never_answers_about_the_future(ahead: int) -> None:
    minute = format_minute(NOW.period, NOW.clock_ms + ahead * 60_000)
    with pytest.raises(ToolInputError) as caught:
        seq_for(LIVE, minute, complete=False)
    now = format_minute(NOW.period, NOW.clock_ms)
    assert str(caught.value) == f"{minute} has not been played yet: the match is at {now}"


@pytest.mark.parametrize("as_of", ["abc", "95'", "45+0'", "0'", "63:00", "-5'"])
def test_a_minute_that_cannot_be_read(as_of: str) -> None:
    with pytest.raises(ToolInputError, match="use a minute such as"):
        seq_for(EVENTS, as_of, complete=True)


def test_no_event_yet() -> None:
    with pytest.raises(ToolInputError, match="has not started"):
        seq_for([], None, complete=False)


def test_parse_a_static_match_id() -> None:
    assert StaticMatches.parse("dominant_loser-42") == ("dominant_loser", 42)
    assert StaticMatches.parse(f"balanced-{MAX_SEED}") == ("balanced", MAX_SEED)


@pytest.mark.parametrize("match_id", ["nope-1", "dominant_loser", "dominant_loser-42~ab12cd34"])
def test_an_unknown_match_names_the_scenarios(match_id: str) -> None:
    with pytest.raises(ToolInputError) as caught:
        StaticMatches.parse(match_id)
    for key in SCENARIOS:
        assert f"{key}-{DEFAULT_SEED}" in str(caught.value)


def test_a_seed_too_large() -> None:
    with pytest.raises(ToolInputError, match="too large"):
        StaticMatches.parse(f"balanced-{MAX_SEED + 1}")


def test_resolve_a_replay_at_full_time() -> None:
    resolved = run(static().resolve("comeback_var-7", None))
    match = generated("comeback_var", 7)
    assert (resolved.source, resolved.match_id, resolved.complete) == (
        "replay",
        "comeback_var-7",
        True,
    )
    assert resolved.as_of_seq == match.events[-1].seq
    assert [m.id for m in resolved.moments] == [m.id for m in detect_all(match.meta, match.events)]


def test_resolve_pins_the_view_and_the_moments_at_a_minute() -> None:
    match = generated("comeback_var", 7)
    resolved = run(static().resolve("comeback_var-7", "HT"))
    ht = next(e.seq for e in match.events if e.type == "period_end" and e.period == 1)
    assert resolved.as_of_seq == ht and resolved.view.last_seq == ht
    assert resolved.as_of_minute == "45+4'"
    prefix = [e for e in match.events if e.seq <= ht]
    assert [m.id for m in resolved.moments] == [m.id for m in detect_all(match.meta, prefix)]


def test_the_cache_keeps_the_latest_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[str, int]] = []
    real = sources._generate

    def counting(scenario: str, seed: int) -> sources._Generated:
        built.append((scenario, seed))
        return real(scenario, seed)

    monkeypatch.setattr(sources, "_generate", counting)
    matches = StaticMatches(size=2)
    for match_id in ("balanced-1", "balanced-2", "balanced-1", "balanced-3", "balanced-1"):
        run(matches.resolve(match_id, None))
    # balanced-2 was the least recently used when balanced-3 came: dropped, the others kept
    assert built == [("balanced", 1), ("balanced", 2), ("balanced", 3)]
    assert len(matches._cache) == 2


def test_concurrent_calls_build_a_match_once(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []
    real = sources._generate

    def counting(scenario: str, seed: int) -> sources._Generated:
        built.append(f"{scenario}-{seed}")
        return real(scenario, seed)

    monkeypatch.setattr(sources, "_generate", counting)
    matches = StaticMatches()

    async def burst() -> list[int]:
        found = await asyncio.gather(*(matches.resolve("balanced-5", None) for _ in range(6)))
        return [view.as_of_seq for view in found]

    seqs = run(burst())
    assert built == ["balanced-5"]
    assert len(set(seqs)) == 1


def test_scenarios_have_their_teams_and_the_default_seed() -> None:
    entries = StaticMatches.scenarios()
    assert [e.key for e in entries] == list(SCENARIOS)
    first = entries[0]
    assert (first.match_id, first.home, first.away) == (
        "dominant_loser-42",
        "Harbor Lions FC",
        "Kestrel Athletic",
    )
    assert all(e.title_en and e.title_vi for e in entries)
    assert StaticMatches().live() == []
