"""Fictional rosters and match metadata construction (docs/day1-design.md section 5)."""

import functools
from importlib import resources

from pydantic import BaseModel, ConfigDict

from pundit.domain.ids import player_id
from pundit.domain.meta import MatchMeta, PlayerInfo, TeamInfo, TeamSheet
from pundit.domain.types import Foot, Formation, Position, Role, Weather


class RosterPlayer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    shirt: int
    name: str
    short_name: str
    position: Position
    role: Role
    foot: Foot
    nationality: str
    starter: bool


class RosterTeam(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    short_name: str
    primary_color: str
    secondary_color: str
    formation: Formation
    venue: str
    players: tuple[RosterPlayer, ...]


class RosterFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    referees: tuple[str, ...]
    teams: tuple[RosterTeam, ...]


@functools.cache
def load_rosters() -> RosterFile:
    text = resources.files("pundit").joinpath("data", "rosters.json").read_text(encoding="utf-8")
    return RosterFile.model_validate_json(text)


def roster_team(team_id: str) -> RosterTeam:
    for team in load_rosters().teams:
        if team.id == team_id:
            return team
    raise KeyError(f"no roster for team {team_id!r}")


def team_sheet(team: RosterTeam) -> tuple[TeamSheet, list[PlayerInfo]]:
    info = TeamInfo(
        id=team.id,
        name=team.name,
        short_name=team.short_name,
        primary_color=team.primary_color,
        secondary_color=team.secondary_color,
        formation=team.formation,
        venue=team.venue,
    )
    players = [
        PlayerInfo(
            id=player_id(team.id, p.shirt),
            team_id=team.id,
            shirt=p.shirt,
            name=p.name,
            short_name=p.short_name,
            position=p.position,
            role=p.role,
            foot=p.foot,
            nationality=p.nationality,
        )
        for p in team.players
    ]
    starters = tuple(pl.id for pl, rp in zip(players, team.players, strict=True) if rp.starter)
    bench = tuple(pl.id for pl, rp in zip(players, team.players, strict=True) if not rp.starter)
    return TeamSheet(team=info, starting_xi=starters, bench=bench), players


def build_meta(
    *,
    match_id: str,
    scenario: str,
    seed: int,
    attempt: int,
    home: str,
    away: str,
    kickoff_utc: str,
    weather: Weather,
    referee: str,
    home_attacks_right_p1: bool,
    generator_version: str,
) -> MatchMeta:
    home_team = roster_team(home)
    home_sheet, home_players = team_sheet(home_team)
    away_sheet, away_players = team_sheet(roster_team(away))
    return MatchMeta(
        generator_version=generator_version,
        match_id=match_id,
        scenario=scenario,
        seed=seed,
        attempt=attempt,
        venue=home_team.venue,
        referee=referee,
        kickoff_utc=kickoff_utc,
        weather=weather,
        home=home_sheet,
        away=away_sheet,
        home_attacks_right_p1=home_attacks_right_p1,
        players={p.id: p for p in home_players + away_players},
    )
