"""Deterministic identifiers (docs/day1-design.md section 3.3; Day 6 session ids, section 6.2).

A session id is what a client uses: a match id for the shared session of a match, or the match
id and a random token for a private one ("dominant_loser-42~k3v9x2ma"). Events, the match meta
and event ids keep the match id: a private session replays the same data.
"""

import secrets
import string
from typing import Annotated

from pydantic import Field

TEAM_ID_PATTERN = r"^[a-z0-9_]+$"
PLAYER_ID_PATTERN = r"^[a-z0-9_]+-\d{2}$"
MATCH_ID_PATTERN = r"^[a-z0-9_]+-\d+$"
EVENT_ID_PATTERN = r"^[a-z0-9_]+-\d+-\d{5}$"
TOKEN_LENGTH = 8
TOKEN_ALPHABET = string.ascii_lowercase + string.digits  # 36**8, about 2.8e12 tokens
TOKEN_PATTERN = rf"^[a-z0-9]{{{TOKEN_LENGTH}}}$"
SESSION_ID_PATTERN = rf"^[a-z0-9_]+-\d+(~[a-z0-9]{{{TOKEN_LENGTH}}})?$"
MAX_SEQ = 99_999

TeamId = Annotated[str, Field(pattern=TEAM_ID_PATTERN)]
PlayerId = Annotated[str, Field(pattern=PLAYER_ID_PATTERN)]


def player_id(team_id: str, shirt: int) -> str:
    if not 1 <= shirt <= 99:
        raise ValueError(f"shirt number must be 1..99, got {shirt}")
    return f"{team_id}-{shirt:02d}"


def match_id(scenario: str, seed: int) -> str:
    if seed < 0:
        raise ValueError(f"seed must be >= 0, got {seed}")
    return f"{scenario}-{seed}"


def event_id(match_id_: str, seq: int) -> str:
    if not 0 <= seq <= MAX_SEQ:
        raise ValueError(f"seq must be 0..{MAX_SEQ}, got {seq}")
    return f"{match_id_}-{seq:05d}"


def new_token() -> str:
    """A private session's token (not data: drawn from the system's secure source)."""
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def session_id(match_id_: str, token: str | None) -> str:
    return match_id_ if token is None else f"{match_id_}~{token}"


def parse_session_id(sid: str) -> tuple[str, str | None]:
    """'dominant_loser-42~k3v9x2ma' -> ('dominant_loser-42', 'k3v9x2ma'); no token -> None."""
    match_part, _, token = sid.partition("~")
    return match_part, token or None


def team_of_player(player: str) -> str:
    """'harbor_lions-09' -> 'harbor_lions'."""
    return player.rsplit("-", 1)[0]
