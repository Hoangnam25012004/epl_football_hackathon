"""Arguments as a model writes them (docs/day6-design.md section 3.4).

Ids, names, short names, "home" / "away", with or without accents; a mistake comes back with
what is valid, so the model can correct it in one more call.
"""

import difflib
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pundit.agents.contracts import MatchContext, PlayerRef, TeamRef
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, MinuteWindow, RollingWindow
from pundit.llm.dto import LlmWindow, to_window
from pundit.mcp_server.errors import ToolInputError
from pundit.stats import REGISTRY
from pundit.stats.registry import MetricSpec

WindowType = Literal[
    "full",
    "first_half",
    "second_half",
    "last_minutes",
    "from_minute",
    "until_minute",
    "minute_range",
]
_CLUB_SUFFIX = re.compile(r" f ?c$")  # "FC" or "F.C." once folded
SIDES = {
    "home": "home",
    "home team": "home",
    "away": "away",
    "away team": "away",
    "doi nha": "home",
    "chu nha": "home",
    "doi khach": "away",
}


class WindowArg(BaseModel):
    """A time window, with the names of the agents' window (llm/dto.py LlmWindow)."""

    model_config = ConfigDict(extra="forbid")

    type: WindowType = Field(
        description="full, first_half, second_half, last_minutes (with minutes), from_minute "
        "(with start_minute), until_minute (with end_minute) or minute_range (with both)"
    )
    minutes: float | None = Field(default=None, gt=0, le=130, description="last_minutes only")
    start_minute: float | None = Field(default=None, ge=0, le=130)
    end_minute: float | None = Field(default=None, gt=0, le=130)


def fold(text: str) -> str:
    """Case, accents, dots and dashes do not matter: 'Phạm Gia Bảo' = 'pham gia bao'."""
    plain = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return " ".join(plain.casefold().replace(".", " ").replace("-", " ").split())


def _with_bare(keys: set[str]) -> set[str]:
    """Each folded name also without a trailing "FC" ("Harbor Lions FC" = "Harbor Lions")."""
    return keys | {_CLUB_SUFFIX.sub("", key) for key in keys}


def _team_keys(team: TeamRef) -> set[str]:
    return _with_bare({fold(n) for n in (team.id, team.name, team.short_name, *team.aliases)})


def resolve_team(context: MatchContext, text: str | None) -> str | None:
    if text is None or not text.strip():
        return None
    key = fold(text)
    for team in context.teams:
        if _with_bare({key}) & _team_keys(team) or SIDES.get(key) == team.side:
            return team.id
    options = "; ".join(f"{t.id} ({t.short_name}, {t.name})" for t in context.teams)
    raise ToolInputError(f"unknown team {text!r}: this match has {options}")


def _names(player: PlayerRef) -> set[str]:
    return {fold(name) for name in (player.name, player.short_name, *player.aliases)}


def _surname_match(player: PlayerRef, key: str) -> bool:
    """'Ellery' or 'J. Ellery' (an initial, then the family name as the roster writes it)."""
    words, mine = key.split(), fold(player.name).split()
    if not words or not mine or words[-1] != mine[-1]:
        return False
    return len(words) == 1 or (len(words) == 2 and len(words[0]) == 1 and mine[0][0] == words[0])


def resolve_player(context: MatchContext, text: str | None) -> str | None:
    if text is None or not text.strip():
        return None
    raw = text.strip()
    if any(player.id == raw for player in context.players):
        return raw
    key = fold(raw)
    found = [p for p in context.players if key in _names(p)]
    if not found:
        found = [p for p in context.players if _surname_match(p, key)]
    if len(found) == 1:
        return found[0].id
    if found:
        listed = "; ".join(f"{p.id} ({p.name})" for p in found[:10])
        raise ToolInputError(f"{text!r} matches several players: {listed}; use the id")
    names = {fold(p.name): p for p in context.players}
    close = difflib.get_close_matches(key, list(names), n=5, cutoff=0.6)
    hint = (
        "did you mean " + "; ".join(f"{names[c].id} ({names[c].name})" for c in close)
        if close
        else "list_players lists both squads"
    )
    raise ToolInputError(f"unknown player {text!r}: {hint}")


def resolve_window(window: WindowArg | None, view: LogView) -> MinuteWindow | RollingWindow:
    if window is None:
        return FULL
    last = view.last
    as_of_ms = last.match_ms if last is not None else 0
    dto = LlmWindow(
        type=window.type,
        minutes=window.minutes,
        start_minute=window.start_minute,
        end_minute=window.end_minute,
    )
    try:
        return to_window(dto, as_of_ms, "window")
    except ValueError as exc:
        raise ToolInputError(str(exc)) from None


def metric_spec(name: str) -> MetricSpec:
    spec = REGISTRY.get(name.strip())
    if spec is not None:
        return spec
    close = difflib.get_close_matches(name.strip(), list(REGISTRY), n=3, cutoff=0.5)
    hint = f"did you mean {', '.join(close)}? " if close else ""
    raise ToolInputError(
        f"unknown metric {name!r}: {hint}list_metrics lists the {len(REGISTRY)} metrics"
    )
