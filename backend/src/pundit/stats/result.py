"""Standard result returned by every metric (docs/day1-design.md section 7.1)."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from pundit.domain.matchtime import Window

Unit = Literal["count", "percent", "xg", "meters", "kmh", "minutes", "ratio", "index", "rating"]
Status = Literal["ok", "insufficient_data"]


class StatResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    team: str | None
    player: str | None
    window: Window
    value: float | None
    unit: Unit
    numerator: float | None = None
    denominator: float | None = None
    evidence_event_ids: tuple[str, ...]
    status: Status
    as_of_seq: int
    definition: str
    label: str | None = None

    @model_validator(mode="after")
    def _check_result(self) -> Self:
        if (self.status == "ok") != (self.value is not None):
            raise ValueError("status 'ok' iff value is set")
        if (
            self.value is not None
            and self.unit == "percent"
            and not -1e-9 <= self.value <= 100.0 + 1e-9
        ):
            raise ValueError(f"percent out of range: {self.value}")
        if (
            self.value is not None
            and self.unit == "count"
            and self.value != len(self.evidence_event_ids)
        ):
            raise ValueError("a count must equal the number of evidence events")
        if tuple(sorted(set(self.evidence_event_ids))) != self.evidence_event_ids:
            raise ValueError("evidence ids must be unique and sorted by seq")
        return self


def display(result: StatResult) -> str:
    """Human-readable value; rounding happens here and nowhere else."""
    if result.value is None:
        return "n/a"
    value = result.value
    match result.unit:
        case "count":
            return f"{value:.0f}"
        case "percent":
            return f"{value:.0f}%"
        case "xg":
            return f"{value:.2f}"
        case "meters":
            return f"{value:.1f} m"
        case "kmh":
            return f"{value:.1f} km/h"
        case "minutes":
            return f"{value:.1f} min"
        case "rating":
            return f"{value:.1f}/10"
        case _:
            return f"{value:.2f}"
