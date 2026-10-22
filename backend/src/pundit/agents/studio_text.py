"""Names and fact lookups shared by the studio templates (talking points, commentary, graphics,
recaps). Players are named by their short name ("T. Calder", "Gia Bảo"): it is one of their
aliases, so the Guardrail recognises who is named, and it is never ambiguous within a match.
"""

from collections.abc import Sequence

from pundit.agents.contracts import Fact, FactPack, Lang, MatchContext


def team_name(match: MatchContext, team_id: str) -> str:
    return match.team(team_id).name


def team_short(match: MatchContext, team_id: str) -> str:
    return match.team(team_id).short_name


def player_name(match: MatchContext, player_id: str) -> str:
    return match.player(player_id).short_name


def other_team(match: MatchContext, team_id: str) -> str:
    return match.opponent(team_id)


def home_away(match: MatchContext) -> tuple[str, str]:
    return match.teams[0].id, match.teams[1].id


def window_part(fact: Fact) -> str:
    """The window label inside a fact key ("FULL", "H1", "52:10-62:10"), without "@seq"."""
    label = fact.key.split("|", 1)[0].split(":", 2)[-1]
    return label.split("@", 1)[0]


def find_fact(
    facts: Sequence[Fact],
    metric: str,
    *,
    team: str | None = None,
    player: str | None = None,
    window: str | None = None,
) -> Fact | None:
    """The first non-derived fact of `metric` for this team / player (and window label)."""
    for fact in facts:
        if fact.derived or fact.metric != metric:
            continue
        if fact.team != team or fact.player != player:
            continue
        if window is not None and window_part(fact) != window:
            continue
        return fact
    return None


def find_key(facts: Sequence[Fact], prefix: str) -> Fact | None:
    """The first fact whose key starts with `prefix` ("shot_xg:", "spell_shots:")."""
    return next((f for f in facts if f.key.startswith(prefix)), None)


def pack_fact(
    pack: FactPack, metric: str, *, team: str | None = None, player: str | None = None
) -> Fact | None:
    return find_fact(pack.facts, metric, team=team, player=player)


def score_line(match: MatchContext, lang: Lang) -> str:
    """ "Harbor Lions FC 1-2 Kestrel Athletic": home first, the order the Guardrail checks."""
    home, away = home_away(match)
    del lang
    return (
        f"{team_name(match, home)} {match.score.get(home, 0)}-{match.score.get(away, 0)} "
        f"{team_name(match, away)}"
    )


def minute_vi(display_minute: str) -> str:
    """ "63'" -> "phút 63"; "45+2'" -> "phút 45+2"."""
    return f"phút {display_minute.rstrip(chr(39))}"


def minute_en(display_minute: str) -> str:
    return display_minute
