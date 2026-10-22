"""Find the teams and players a claim talks about. Never guesses (docs/day2-design.md 4.2.3).

A name that matches two players is reported as ambiguous; the analyst then refuses the claim
with `ambiguous_entity` instead of picking one.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pundit.agents.contracts import MatchContext
from pundit.agents.language import Term, Text, find_terms

EntityKind = Literal["team", "player", "both_teams"]

HOME_PHRASES = (
    "đội nhà", "đội chủ nhà", "chủ nhà", "home side", "home team", "the hosts", "hosts",
)  # fmt: skip
AWAY_PHRASES = (
    "đội khách", "đội bóng khách", "đội làm khách", "away side", "away team", "the visitors",
    "visitors", "visiting side", "visiting team",
)  # fmt: skip
BOTH_PHRASES = (
    "hai đội", "cả hai đội", "hai đội bóng", "hai bên", "cả hai bên", "both teams",
    "both sides", "the two teams", "the two sides",
)  # fmt: skip


@dataclass(frozen=True)
class EntityTerm:
    kind: EntityKind | Literal["mixed"]
    ids: tuple[str, ...]  # team ids or player ids; more than one = ambiguous


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    kind: EntityKind
    team: str | None = None
    player: str | None = None
    candidates: tuple[str, ...] = ()  # every id the words match when ambiguous
    phrase: Literal["name", "home", "away", "both"] = "name"

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


Roster = tuple[tuple[tuple[str, tuple[str, ...]], ...], tuple[tuple[str, tuple[str, ...]], ...]]


def entity_terms(ctx: MatchContext) -> tuple[Term[EntityTerm], ...]:
    """Terms of every name; built once per roster (the clock and score of `ctx` do not
    matter), since every check of every studio text looks names up."""
    roster: Roster = (
        tuple((team.id, tuple(team.aliases)) for team in ctx.teams),
        tuple((player.id, tuple(player.aliases)) for player in ctx.players),
    )
    return _entity_terms(roster)


@lru_cache(maxsize=32)
def _entity_terms(roster: Roster) -> tuple[Term[EntityTerm], ...]:
    teams, players = roster
    owners: dict[str, list[tuple[str, str]]] = {}
    for team_id, aliases in teams:
        for alias in aliases:
            owners.setdefault(alias, []).append(("team", team_id))
    for player_id, aliases in players:
        for alias in aliases:
            owners.setdefault(alias, []).append(("player", player_id))
    terms: list[Term[EntityTerm]] = []
    for alias, entries in owners.items():
        kinds = {kind for kind, _ in entries}
        ids = tuple(sorted({entity for _, entity in entries}))
        kind: EntityKind | Literal["mixed"] = "mixed" if len(kinds) > 1 else entries[0][0]  # type: ignore[assignment]
        terms.append(Term(alias, EntityTerm(kind, ids)))
    home, away = teams[0][0], teams[1][0]
    terms += [Term(p, EntityTerm("team", (home,))) for p in HOME_PHRASES]
    terms += [Term(p, EntityTerm("team", (away,))) for p in AWAY_PHRASES]
    terms += [Term(p, EntityTerm("both_teams", ())) for p in BOTH_PHRASES]
    return tuple(terms)


def _phrase(term: Term[EntityTerm]) -> Literal["name", "home", "away", "both"]:
    if term.phrase in HOME_PHRASES:
        return "home"
    if term.phrase in AWAY_PHRASES:
        return "away"
    if term.phrase in BOTH_PHRASES:
        return "both"
    return "name"


def find_entities(text: Text, ctx: MatchContext) -> list[Mention]:
    mentions: list[Mention] = []
    for hit in find_terms(text, entity_terms(ctx)):
        entity = hit.payload
        phrase = _phrase(hit.term)
        if entity.kind == "both_teams":
            mentions.append(Mention(hit.start, hit.end, "both_teams", phrase=phrase))
        elif len(entity.ids) > 1 or entity.kind == "mixed":
            kind: EntityKind = "player" if entity.kind == "player" else "team"
            mentions.append(Mention(hit.start, hit.end, kind, candidates=entity.ids))
        elif entity.kind == "team":
            team = entity.ids[0]
            mentions.append(Mention(hit.start, hit.end, "team", team=team, phrase=phrase))
        else:
            player = entity.ids[0]
            team = ctx.player(player).team
            mentions.append(Mention(hit.start, hit.end, "player", team=team, player=player))
    return mentions
