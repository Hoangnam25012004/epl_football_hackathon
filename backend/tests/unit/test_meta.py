import unicodedata

import pytest
from builders import MatchBuilder
from pydantic import ValidationError

from pundit.domain.errors import UnknownPlayerError, UnknownTeamError
from pundit.domain.meta import MatchMeta, TeamSheet
from pundit.engine.rosters import load_rosters


@pytest.fixture
def meta() -> MatchMeta:
    return MatchBuilder(start=False).meta


def test_rosters_are_fictional_and_well_formed() -> None:
    rosters = load_rosters()
    assert len(rosters.teams) == 4
    for team in rosters.teams:
        assert len(team.players) == 20
        assert len({p.shirt for p in team.players}) == 20
        starters = [p for p in team.players if p.starter]
        assert len(starters) == 11
        assert sum(p.position == "GK" for p in starters) == 1
        assert sum(p.nationality == "VN" for p in team.players) == 1
        for player in team.players:
            assert unicodedata.is_normalized("NFC", player.name)


def test_helpers(meta: MatchMeta) -> None:
    home, away = meta.team_ids
    assert meta.opponent(home) == away
    assert meta.side_of(away) == "away"
    assert meta.team_for("home") == home
    assert meta.player_info(f"{home}-09").name == "Tobias Calder"
    with pytest.raises(UnknownTeamError):
        meta.check_team("nobody")
    with pytest.raises(UnknownPlayerError):
        meta.player_info(f"{home}-99")


def test_sheet_needs_eleven_unique_players(meta: MatchMeta) -> None:
    sheet = meta.home
    with pytest.raises(ValidationError):
        TeamSheet(team=sheet.team, starting_xi=sheet.starting_xi[:10], bench=sheet.bench)
    with pytest.raises(ValidationError):
        TeamSheet(
            team=sheet.team,
            starting_xi=(*sheet.starting_xi[:10], sheet.starting_xi[0]),
            bench=sheet.bench,
        )


def test_starting_xi_needs_exactly_one_goalkeeper(meta: MatchMeta) -> None:
    sheet = meta.home
    keeper = next(p for p in sheet.starting_xi if meta.players[p].position == "GK")
    reserve_keeper = next(p for p in sheet.bench if meta.players[p].position == "GK")
    outfield = next(p for p in sheet.bench if meta.players[p].position != "GK")
    swapped = TeamSheet(
        team=sheet.team,
        starting_xi=tuple(outfield if p == keeper else p for p in sheet.starting_xi),
        bench=tuple(keeper if p == outfield else p for p in sheet.bench),
    )
    with pytest.raises(ValidationError):
        MatchMeta.model_validate({**meta.model_dump(), "home": swapped.model_dump()})
    two_keepers = TeamSheet(
        team=sheet.team,
        starting_xi=tuple(
            reserve_keeper if p == sheet.starting_xi[-1] else p for p in sheet.starting_xi
        ),
        bench=tuple(sheet.starting_xi[-1] if p == reserve_keeper else p for p in sheet.bench),
    )
    with pytest.raises(ValidationError):
        MatchMeta.model_validate({**meta.model_dump(), "home": two_keepers.model_dump()})


def test_names_must_be_nfc(meta: MatchMeta) -> None:
    pid = next(iter(meta.players))
    data = meta.model_dump()
    data["players"][pid]["name"] = unicodedata.normalize("NFD", "Phạm Gia Bảo")
    with pytest.raises(ValidationError):
        MatchMeta.model_validate(data)


def test_meta_is_marked_synthetic(meta: MatchMeta) -> None:
    assert meta.synthetic is True
    assert "Synthetic" in meta.disclaimer
    assert "Premier League" not in meta.competition
