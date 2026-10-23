"""Arguments as a model writes them (docs/day6-design.md sections 3.4 and 10.2)."""

import pytest
from helpers import match_context, view_at
from pydantic import ValidationError

from pundit.agents.contracts import MatchContext
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow
from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.params import (
    WindowArg,
    fold,
    metric_spec,
    resolve_player,
    resolve_team,
    resolve_window,
)
from pundit.stats import REGISTRY

DL = match_context()  # dominant_loser-42 at full time
CV = match_context("comeback_var", 7)
VIEW = view_at()


def renamed(context: MatchContext, player_id: str, name: str, *aliases: str) -> MatchContext:
    """The same match with one player renamed (the rosters have no two players alike)."""
    players = tuple(
        p.model_copy(update={"name": name, "short_name": aliases[0], "aliases": (name, *aliases)})
        if p.id == player_id
        else p
        for p in context.players
    )
    return context.model_copy(update={"players": players})


TWINS = renamed(DL, "harbor_lions-20", "Jamie Ellery", "J. Ellery", "Ellery")


def test_fold_ignores_case_accents_dots_and_dashes() -> None:
    assert fold("Phạm Gia Bảo") == "pham gia bao"
    assert fold("ĐỘI NHÀ") == "doi nha"
    assert fold("  J.Ellery ") == "j ellery"
    assert fold("Kofi Mensah-Adu") == "kofi mensah adu"


@pytest.mark.parametrize(
    "text",
    ["harbor_lions", "HAR", "har", "Harbor Lions FC", "harbor lions", "HARBOR LIONS FC",
     "Harbor", "Lions", "home", "Home team", "đội nhà", "Đội Nhà", "chủ nhà"],
)  # fmt: skip
def test_home_team_spellings(text: str) -> None:
    assert resolve_team(DL, text) == "harbor_lions"


@pytest.mark.parametrize(
    "text",
    ["kestrel_athletic", "KES", "Kestrel", "Kestrel Athletic", "Kestrel Athletic FC",
     "kestrel athletic f.c.", "away", "đội khách", "Doi khach"],
)  # fmt: skip
def test_away_team_spellings(text: str) -> None:
    assert resolve_team(DL, text) == "kestrel_athletic"


def test_teams_of_another_match() -> None:
    assert resolve_team(CV, "Ironvale") == "ironvale_united"
    assert resolve_team(CV, "Rovers") == "saltmarsh_rovers"
    assert resolve_team(CV, "home") == "ironvale_united"


@pytest.mark.parametrize("text", [None, "", "   "])
def test_no_team(text: str | None) -> None:
    assert resolve_team(DL, text) is None


@pytest.mark.parametrize("text", ["Kestrel FC Wrong", "Ironvale", "FC", "visitors from mars"])
def test_an_unknown_team_lists_the_two_teams_of_the_match(text: str) -> None:
    with pytest.raises(ToolInputError) as caught:
        resolve_team(DL, text)
    message = str(caught.value)
    assert message.startswith(f"unknown team {text!r}")
    assert "harbor_lions (HAR, Harbor Lions FC)" in message
    assert "kestrel_athletic (KES, Kestrel Athletic)" in message


@pytest.mark.parametrize(
    "text",
    ["kestrel_athletic-11", "Jordan Ellery", "jordan ellery", "J. Ellery", "J.Ellery", "j ellery",
     "Ellery", "ELLERY"],
)  # fmt: skip
def test_player_spellings(text: str) -> None:
    assert resolve_player(DL, text) == "kestrel_athletic-11"


@pytest.mark.parametrize(
    ("text", "player"),
    [
        ("Phạm Gia Bảo", "harbor_lions-06"),
        ("Pham Gia Bao", "harbor_lions-06"),
        ("Gia Bảo", "harbor_lions-06"),
        ("gia bao", "harbor_lions-06"),
        ("Bảo", "harbor_lions-06"),  # the given name, as Vietnamese commentators say it
        ("Minh Khôi", "kestrel_athletic-04"),
        ("Khôi", "kestrel_athletic-04"),
        ("K. Mensah-Adu", "harbor_lions-05"),
        ("Mensah Adu", "harbor_lions-05"),
    ],
)
def test_names_with_accents_and_dashes(text: str, player: str) -> None:
    assert resolve_player(DL, text) == player


@pytest.mark.parametrize("text", [None, "", "  "])
def test_no_player(text: str | None) -> None:
    assert resolve_player(DL, text) is None


@pytest.mark.parametrize("text", ["Ellery", "J. Ellery", "j ellery"])
def test_a_shared_name_lists_the_candidates(text: str) -> None:
    with pytest.raises(ToolInputError) as caught:
        resolve_player(TWINS, text)
    message = str(caught.value)
    assert "matches several players" in message
    assert "harbor_lions-20 (Jamie Ellery)" in message
    assert "kestrel_athletic-11 (Jordan Ellery)" in message
    assert message.endswith("use the id")


def test_a_full_name_or_an_id_tells_the_twins_apart() -> None:
    assert resolve_player(TWINS, "Jamie Ellery") == "harbor_lions-20"
    assert resolve_player(TWINS, "Jordan Ellery") == "kestrel_athletic-11"
    assert resolve_player(TWINS, "kestrel_athletic-11") == "kestrel_athletic-11"


def test_a_wrong_initial_is_not_a_match() -> None:
    with pytest.raises(ToolInputError, match="unknown player"):
        resolve_player(DL, "X. Ellery")


def test_a_misspelt_player_gets_the_closest_names() -> None:
    with pytest.raises(ToolInputError) as caught:
        resolve_player(DL, "Jordan Elery")
    assert "did you mean kestrel_athletic-11 (Jordan Ellery)" in str(caught.value)


def test_an_unknown_player_points_to_list_players() -> None:
    with pytest.raises(ToolInputError, match="list_players lists both squads"):
        resolve_player(DL, "Nobody Atall Zzyzx")


def test_no_window_is_the_whole_match() -> None:
    assert resolve_window(None, VIEW) == FULL


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (WindowArg(type="full"), FULL),
        (WindowArg(type="first_half"), H1),
        (WindowArg(type="second_half"), H2),
        (WindowArg(type="from_minute", start_minute=60), MinuteWindow(start_min=60)),
        (WindowArg(type="until_minute", end_minute=30), MinuteWindow(end_min=30)),
        (
            WindowArg(type="minute_range", start_minute=15, end_minute=30),
            MinuteWindow(start_min=15, end_min=30),
        ),
    ],
)
def test_window_types(window: WindowArg, expected: MinuteWindow) -> None:
    assert resolve_window(window, VIEW) == expected


def test_last_minutes_ends_at_the_latest_event() -> None:
    last = VIEW.last
    assert last is not None
    window = resolve_window(WindowArg(type="last_minutes", minutes=15), VIEW)
    assert window == RollingWindow.last(15, last.match_ms)


@pytest.mark.parametrize(
    ("window", "message"),
    [
        (WindowArg(type="last_minutes"), "needs minutes"),
        (WindowArg(type="from_minute"), "needs start_minute"),
        (WindowArg(type="until_minute"), "needs end_minute"),
        (WindowArg(type="minute_range", start_minute=30), "needs end_minute"),
        (WindowArg(type="minute_range", start_minute=30, end_minute=15), "start_minute < end"),
    ],
)
def test_an_incomplete_window_says_what_is_missing(window: WindowArg, message: str) -> None:
    with pytest.raises(ToolInputError, match=message):
        resolve_window(window, VIEW)


@pytest.mark.parametrize(
    "value",
    [{"type": "full", "half": 2}, {"type": "third_half"}, {"type": "last_minutes", "minutes": -5}],
)
def test_the_window_schema_refuses_what_it_does_not_know(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WindowArg.model_validate(value)


def test_metric_names() -> None:
    assert metric_spec("possession").name == "possession"
    assert metric_spec("  xg ").name == "xg"


def test_a_misspelt_metric_gets_the_closest_names() -> None:
    with pytest.raises(ToolInputError, match="did you mean possession"):
        metric_spec("posession")


def test_an_unknown_metric_points_to_list_metrics() -> None:
    with pytest.raises(ToolInputError) as caught:
        metric_spec("goals_per_banana")
    assert f"list_metrics lists the {len(REGISTRY)} metrics" in str(caught.value)
    assert "did you mean" not in str(caught.value)
