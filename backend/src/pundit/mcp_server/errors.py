"""Errors of the MCP tools (docs/day6-design.md section 3.4)."""

from pundit.domain.errors import PunditError


class ToolInputError(PunditError):
    """A request the calling model can correct: the message says what is valid."""
