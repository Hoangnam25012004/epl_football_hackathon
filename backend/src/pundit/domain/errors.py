"""Error types. Invariant violations carry the INV code from docs/day1-design.md section 10."""


class PunditError(Exception):
    """Base class for every error raised by pundit."""


class InvalidEventError(PunditError):
    """An event breaks one of the match invariants (INV-xx)."""

    def __init__(self, code: str, event_id: str | None, detail: str) -> None:
        super().__init__(f"{code} [{event_id or '-'}]: {detail}")
        self.code = code
        self.event_id = event_id
        self.detail = detail


class UnknownTeamError(PunditError, ValueError):
    """A team id that is not part of the match."""


class UnknownPlayerError(PunditError, ValueError):
    """A player id that is not part of the match."""


class UnknownMetricError(PunditError, ValueError):
    """A metric name that is not registered."""


class ScenarioError(PunditError, ValueError):
    """A scenario definition is inconsistent."""


class ScenarioUnsatisfiableError(PunditError):
    """The generator could not satisfy the scenario expectations within the attempt budget."""
