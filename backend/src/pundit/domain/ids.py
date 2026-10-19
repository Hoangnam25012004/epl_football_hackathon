"""Deterministic identifiers (docs/day1-design.md section 3.3)."""

from typing import Annotated

from pydantic import Field

TEAM_ID_PATTERN = r"^[a-z0-9_]+$"
PLAYER_ID_PATTERN = r"^[a-z0-9_]+-\d{2}$"
MATCH_ID_PATTERN = r"^[a-z0-9_]+-\d+$"
EVENT_ID_PATTERN = r"^[a-z0-9_]+-\d+-\d{5}$"
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


def team_of_player(player: str) -> str:
    """'harbor_lions-09' -> 'harbor_lions'."""
    return player.rsplit("-", 1)[0]
