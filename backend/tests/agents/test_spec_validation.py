"""validate_spec and the structural validators of the spec models (design section 4.3)."""

import pytest
from helpers import match_context
from pydantic import ValidationError

from pundit.agents.contracts import Assertion, ClaimSpec, Subject
from pundit.agents.language import Text
from pundit.agents.spec_validation import validate_spec
from pundit.domain.matchtime import H2

HAR = Subject(kind="team", team="harbor_lions")
KES = Subject(kind="team", team="kestrel_athletic")
CALDER = Subject(kind="player", player="harbor_lions-09")
BOTH = Subject(kind="both_teams")


def spec(*assertions: Assertion) -> ClaimSpec:
    return ClaimSpec(language="vi", assertions=assertions)


def errors(*assertions: Assertion, claim: str | None = None) -> list[str]:
    text = Text.of(claim) if claim is not None else None
    return validate_spec(spec(*assertions), match_context(), claim=text)


def test_a_valid_spec_has_no_errors() -> None:
    assert errors(Assertion(kind="dominance", subject=HAR, window=H2)) == []
    shots = Assertion(kind="compare", subject=HAR, other=KES, metric="shots", op=">", window=H2)
    assert errors(shots, claim="HAR có nhiều cú sút hơn KES") == []


@pytest.mark.parametrize(
    ("assertion", "message"),
    [
        (
            Assertion(kind="value", subject=HAR, metric="possesion", op="==", value=60, window=H2),
            "unknown metric",
        ),
        (
            Assertion(
                kind="value", subject=CALDER, metric="possession", op="==", value=60, window=H2
            ),
            "cannot be measured for a player",
        ),
        (
            Assertion(
                kind="value", subject=HAR, metric="ball_in_play_time", op="==", value=20, window=H2
            ),
            "match-level metric",
        ),
        (
            Assertion(
                kind="value", subject=HAR, metric="player_distance", op="==", value=9000, window=H2
            ),
            "player metric",
        ),
        (Assertion(kind="dominance", subject=CALDER, window=H2), "only for a team"),
        (
            Assertion(kind="compare", subject=HAR, other=HAR, metric="shots", op=">", window=H2),
            "must differ",
        ),
        (
            Assertion(kind="compare", subject=HAR, other=CALDER, metric="shots", op=">", window=H2),
            "team with a team",
        ),
        (
            Assertion(kind="compare", subject=HAR, metric="shots", op=">", ratio=0.5, window=H2),
            "ratio must be > 1",
        ),
        (
            Assertion(kind="compare", subject=BOTH, metric="fouls", op=">", window=H2),
            "only works with an equality",
        ),
        (
            Assertion(kind="value", subject=HAR, metric="shots", op="==", value=2.5, window=H2),
            "whole number",
        ),
        (
            Assertion(
                kind="value", subject=HAR, metric="possession", op="==", value=120, window=H2
            ),
            "cannot exceed 100",
        ),
        (
            Assertion(
                kind="value",
                subject=Subject(kind="team", team="ironvale_united"),
                metric="shots",
                op="==",
                value=2,
                window=H2,
            ),
            "not in this match",
        ),
        (
            Assertion(kind="value", subject=HAR, metric="shots", op="~", value=2, window=H2),
            "approx",
        ),
    ],
)
def test_semantic_errors(assertion: Assertion, message: str) -> None:
    found = errors(assertion)
    assert any(message in e for e in found), found


def test_grounding_players_must_be_named() -> None:
    calder_scored = Assertion(
        kind="value", subject=CALDER, metric="score", op=">=", value=1, window=H2
    )
    assert errors(calder_scored, claim="Calder ghi bàn") == []
    assert any("not named" in e for e in errors(calder_scored, claim="Ribas ghi bàn"))


def test_grounding_team_claims_need_a_team() -> None:
    dominance = Assertion(kind="dominance", subject=HAR, window=H2)
    assert errors(dominance, claim="Đội nhà áp đảo") == []
    assert any("does not name a team" in e for e in errors(dominance, claim="áp đảo hiệp 2"))


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "dominance", "subject": HAR, "metric": "shots"},
        {"kind": "compare", "subject": HAR, "op": ">"},
        {"kind": "compare", "subject": HAR, "metric": "shots", "op": ">", "value": 3},
        {"kind": "value", "subject": HAR, "metric": "shots", "op": "=="},
        {"kind": "value", "subject": HAR, "metric": "shots", "op": "==", "value": 1, "other": KES},
    ],
)
def test_shapes_are_checked_by_the_model(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Assertion(window=H2, **fields)  # type: ignore[arg-type]


def test_subject_and_spec_invariants() -> None:
    with pytest.raises(ValidationError):
        Subject(kind="team")
    with pytest.raises(ValidationError):
        Subject(kind="player", player="harbor_lions-09", team="kestrel_athletic")
    with pytest.raises(ValidationError):
        Subject(kind="both_teams", team="harbor_lions")
    with pytest.raises(ValidationError):
        ClaimSpec(language="vi", assertions=())  # empty needs a reason
    with pytest.raises(ValidationError):
        ClaimSpec(
            language="vi",
            assertions=(Assertion(kind="dominance", subject=HAR, window=H2),),
            unsupported_reason="unsupported_claim",
        )
    with pytest.raises(ValidationError):
        ClaimSpec(
            language="vi",
            assertions=tuple(Assertion(kind="dominance", subject=HAR, window=H2) for _ in range(4)),
        )
