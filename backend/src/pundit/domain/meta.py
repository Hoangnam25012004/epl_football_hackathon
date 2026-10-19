"""Match metadata: teams, line-ups and players (docs/day1-design.md section 5)."""

import unicodedata
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pundit.domain.errors import UnknownPlayerError, UnknownTeamError
from pundit.domain.ids import MATCH_ID_PATTERN, PlayerId, TeamId, player_id, team_of_player
from pundit.domain.types import Foot, Formation, Position, Role, Side, Weather

DISCLAIMER = "Synthetic data. Fictional teams and players. Not affiliated with the Premier League."


def _check_nfc(*values: str) -> None:
    for value in values:
        if not unicodedata.is_normalized("NFC", value):
            raise ValueError(f"{value!r} is not NFC-normalized")


class PlayerInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: PlayerId
    team_id: TeamId
    shirt: int = Field(ge=1, le=99)
    name: str = Field(min_length=1)
    short_name: str = Field(min_length=1)
    position: Position
    role: Role
    foot: Foot
    nationality: str = Field(pattern=r"^[A-Z]{2}$")

    @model_validator(mode="after")
    def _check_player(self) -> Self:
        if self.id != player_id(self.team_id, self.shirt):
            raise ValueError(f"player id {self.id} does not match team/shirt")
        _check_nfc(self.name, self.short_name)
        return self


class TeamInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TeamId
    name: str = Field(min_length=1)
    short_name: str = Field(pattern=r"^[A-Z]{3}$")
    primary_color: str = Field(pattern=r"^#[0-9A-F]{6}$")
    secondary_color: str = Field(pattern=r"^#[0-9A-F]{6}$")
    formation: Formation
    venue: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_team(self) -> Self:
        _check_nfc(self.name, self.venue)
        return self


class TeamSheet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team: TeamInfo
    starting_xi: tuple[PlayerId, ...]
    bench: tuple[PlayerId, ...]

    @model_validator(mode="after")
    def _check_sheet(self) -> Self:
        if len(self.starting_xi) != 11:
            raise ValueError(f"{self.team.id}: starting XI must have 11 players")
        if len(self.bench) > 9:
            raise ValueError(f"{self.team.id}: bench has more than 9 players")
        everyone = self.starting_xi + self.bench
        if len(set(everyone)) != len(everyone):
            raise ValueError(f"{self.team.id}: duplicate player in team sheet")
        for pid in everyone:
            if team_of_player(pid) != self.team.id:
                raise ValueError(f"{pid} does not belong to {self.team.id}")
        return self


class MatchMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    generator_version: str
    match_id: str = Field(pattern=MATCH_ID_PATTERN)
    scenario: str
    seed: int = Field(ge=0)
    attempt: int = Field(ge=0)
    synthetic: Literal[True] = True
    disclaimer: str = DISCLAIMER
    competition: str = "Synthetic League"
    venue: str
    referee: str
    kickoff_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    weather: Weather
    home: TeamSheet
    away: TeamSheet
    home_attacks_right_p1: bool
    players: dict[PlayerId, PlayerInfo]

    @model_validator(mode="after")
    def _check_meta(self) -> Self:
        if self.home.team.id == self.away.team.id:
            raise ValueError("home and away must be different teams")
        sheet_players = set(self.home.starting_xi + self.home.bench)
        sheet_players |= set(self.away.starting_xi + self.away.bench)
        if sheet_players != set(self.players):
            raise ValueError("players must contain exactly the players of both team sheets")
        for pid, info in self.players.items():
            if pid != info.id:
                raise ValueError(f"players key {pid} does not match {info.id}")
        for sheet in (self.home, self.away):
            keepers = [p for p in sheet.starting_xi if self.players[p].position == "GK"]
            if len(keepers) != 1:
                raise ValueError(f"{sheet.team.id}: starting XI needs exactly one goalkeeper")
        _check_nfc(self.venue, self.referee)
        return self

    @property
    def team_ids(self) -> tuple[str, str]:
        return self.home.team.id, self.away.team.id

    def check_team(self, team: str) -> None:
        if team not in self.team_ids:
            raise UnknownTeamError(f"unknown team {team!r}; expected one of {self.team_ids}")

    def opponent(self, team: str) -> str:
        self.check_team(team)
        return self.away.team.id if team == self.home.team.id else self.home.team.id

    def side_of(self, team: str) -> Side:
        self.check_team(team)
        return "home" if team == self.home.team.id else "away"

    def team_for(self, side: Side) -> str:
        return self.home.team.id if side == "home" else self.away.team.id

    def sheet(self, team: str) -> TeamSheet:
        self.check_team(team)
        return self.home if team == self.home.team.id else self.away

    def team_info(self, team: str) -> TeamInfo:
        return self.sheet(team).team

    def player_info(self, player: str) -> PlayerInfo:
        info = self.players.get(player)
        if info is None:
            raise UnknownPlayerError(f"unknown player {player!r}")
        return info
