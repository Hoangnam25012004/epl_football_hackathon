"""MatchContext: names, aliases, score, clock and metric catalog at a pinned `as_of_seq`."""

from functools import cache

from pundit.agents.contracts import MatchContext, MetricRef, PlayerRef, TeamRef
from pundit.domain.log import LogView
from pundit.domain.matchtime import format_minute
from pundit.domain.meta import MatchMeta, PlayerInfo, TeamInfo, TeamSheet
from pundit.domain.types import Side
from pundit.stats import REGISTRY, MetricParams, compute

# Words of a club name that do not identify the club on their own.
GENERIC_TEAM_WORDS = frozenset(
    {"fc", "afc", "cf", "sc", "club", "athletic", "united", "city", "town", "the"}
)
# "Lower is better / stronger": the comparative words of a claim flip for these metrics.
NEGATIVE_POLARITY = frozenset({"ppda"})


def team_aliases(team: TeamInfo) -> tuple[str, ...]:
    words = team.name.split()
    aliases = [team.name, team.short_name]
    if words and words[-1].lower() in ("fc", "afc", "cf"):
        aliases.append(" ".join(words[:-1]))
    aliases += [w for w in words if w.lower() not in GENERIC_TEAM_WORDS]
    return tuple(dict.fromkeys(aliases))


def player_aliases(player: PlayerInfo) -> tuple[str, ...]:
    """Full name, short name and, except for Vietnamese names, the family name.

    A single Vietnamese syllable ("Bảo", "Phong") is never an alias: folded, it collides with
    common words ("bao nhiêu", "hai").
    """
    aliases = [player.name, player.short_name]
    if player.nationality != "VN":
        aliases.append(player.name.split()[-1])
    return tuple(dict.fromkeys(aliases))


@cache
def metric_catalog() -> tuple[MetricRef, ...]:
    return tuple(
        MetricRef(
            name=spec.name,
            scope=spec.scope,
            unit=spec.unit,
            supports_player=spec.supports_player,
            polarity=-1 if spec.name in NEGATIVE_POLARITY else 1,
            description_vi=spec.description_vi,
            description_en=spec.description_en,
        )
        for spec in REGISTRY.values()
    )


def _refs(meta: MatchMeta) -> tuple[tuple[TeamRef, ...], tuple[PlayerRef, ...]]:
    sides: tuple[tuple[Side, TeamSheet], ...] = (("home", meta.home), ("away", meta.away))
    teams = tuple(
        TeamRef(
            id=sheet.team.id,
            name=sheet.team.name,
            short_name=sheet.team.short_name,
            side=side,
            aliases=team_aliases(sheet.team),
        )
        for side, sheet in sides
    )
    players = tuple(
        PlayerRef(
            id=info.id,
            team=info.team_id,
            shirt=info.shirt,
            name=info.name,
            short_name=info.short_name,
            aliases=player_aliases(info),
        )
        for info in sorted(meta.players.values(), key=lambda p: p.id)
    )
    return teams, players


def build_match_context(view: LogView) -> MatchContext:
    meta = view.meta
    teams, players = _refs(meta)
    last = view.last
    score = {
        team: int(compute(view, "score", MetricParams(team=team)).value or 0)
        for team in meta.team_ids
    }
    finished = last is not None and (
        last.type == "match_end" or (last.type == "period_end" and last.period == 2)
    )
    return MatchContext(
        match_id=meta.match_id,
        as_of_seq=view.last_seq,
        as_of_ms=last.match_ms if last is not None else 0,
        as_of_clock_ms=last.clock_ms if last is not None else 0,
        period=last.period if last is not None and last.type != "match_start" else 0,
        clock_display=format_minute(last.period, last.clock_ms) if last is not None else "0'",
        finished=finished,
        teams=teams,
        players=players,
        score=score,
        metrics=metric_catalog(),
    )
