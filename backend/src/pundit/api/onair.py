"""On-air control of the lower-third overlay (docs/day4-design.md section 3.2).

Every overlay schedules the lower-thirds by itself (auto mode); an editor can take one on air,
hold the one showing or clear it. The server keeps only what a person decided, so every overlay
and the studio monitor apply the same decision; `revision` orders the decisions and
`last_action` lets a client tell a "clear" in auto mode (skip what is showing) from the rest.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OnAirAction = Literal["take", "clear", "hold", "release", "auto"]
MAX_ITEM_ID = 128


class OnAirState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["auto", "manual"] = "auto"
    item_id: str | None = None  # the lower-third taken on air (manual mode)
    hold: bool = False  # keep what is showing; nothing replaces it until released
    revision: int = Field(default=0, ge=0)  # +1 per decision; clients ignore older ones
    last_action: OnAirAction | None = None
    at_match_ms: int = Field(default=0, ge=0)  # match time of the decision


class OnAirCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: OnAirAction
    item_id: str | None = Field(default=None, min_length=1, max_length=MAX_ITEM_ID)


class OnAirError(ValueError):
    """A command that cannot apply (a take without an item)."""


def apply_command(state: OnAirState, command: OnAirCommand, at_match_ms: int) -> OnAirState:
    """The state after one editor decision (the caller checks that a taken item exists)."""
    changes: dict[str, object]
    match command.action:
        case "take":
            if command.item_id is None:
                raise OnAirError("take needs the id of a lower-third")
            changes = {"mode": "manual", "item_id": command.item_id, "hold": False}
        case "clear":
            # manual: the screen goes blank until the next take; auto: skip what is showing
            changes = {"item_id": None, "hold": False}
        case "hold":
            changes = {"hold": True}
        case "release":
            changes = {"hold": False}
        case "auto":
            changes = {"mode": "auto", "item_id": None, "hold": False}
    return state.model_copy(
        update={
            **changes,
            "revision": state.revision + 1,
            "last_action": command.action,
            "at_match_ms": max(0, at_match_ms),
        }
    )
