"""Typed payloads of the WebSocket messages (docs/day4-design.md section 3.8).

The hub sends dicts; these models describe them for the JSON Schema the frontend generates its
types from (`ws_payloads.schema.json`), and contract tests validate recorded messages with them.
`WsPayloads` has one field per message type.
"""

from pydantic import BaseModel, ConfigDict

from pundit.agents.contracts import (
    ClaimResult,
    CommentaryView,
    LowerThirdView,
    Moment,
    Profile,
    RecapView,
    TalkingPoint,
    TalkingPointStatus,
)
from pundit.api.onair import OnAirState
from pundit.api.routes import MatchInfo
from pundit.api.sessions import SessionStatus
from pundit.domain.events import Event
from pundit.state.match_state import MatchSnapshot


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HelloPayload(_Payload):
    """`hello` / `resync`: the Day 2 state, plus the studio items rendered for the viewer's
    profile when the studio is on."""

    match: MatchInfo
    snapshot: MatchSnapshot
    moments: list[Moment]
    claims: list[ClaimResult]
    degraded: list[str]
    on_air: OnAirState | None = None
    profile: Profile | None = None
    talking_points: list[TalkingPoint] | None = None
    lower_thirds: list[LowerThirdView] | None = None
    commentary: list[CommentaryView] | None = None
    recaps: list[RecapView] | None = None


class StatusPayload(_Payload):
    session: SessionStatus
    error: str | None
    degraded: list[str]
    speed: float
    restarted: bool  # the match id now names a new session: reconnect


class TalkingPointStatusPayload(_Payload):
    id: str
    status: TalkingPointStatus


class WsPayloads(_Payload):
    hello: HelloPayload
    resync: HelloPayload
    event: Event
    snapshot: MatchSnapshot
    moment: Moment
    claim: ClaimResult
    status: StatusPayload
    talking_point: TalkingPoint
    talking_point_status: TalkingPointStatusPayload
    lower_third: LowerThirdView
    commentary: CommentaryView
    recap: RecapView
    on_air: OnAirState
