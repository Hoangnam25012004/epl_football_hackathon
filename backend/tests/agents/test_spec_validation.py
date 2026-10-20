"""validate_spec and the structural validators of the spec models (design section 4.3)."""

import pytest
from helpers import match_context
from pydantic import ValidationError

from pundit.agents.contracts import Assertion, ClaimSpec, Subject
from pundit.agents.language import Text
from pundit.agents.spec_validation import validate_spec
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow

HAR = Subject(kind="team", team="harbor_lions")
LAST_MS = match_context().as_of_ms
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
    assert errors(shots, claim="HAR có nhiều cú sút hơn KES trong hiệp 2") == []


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
    assert errors(calder_scored, claim="Calder ghi bàn trong hiệp 2") == []
    assert any("not named" in e for e in errors(calder_scored, claim="Ribas ghi bàn trong hiệp 2"))


def test_grounding_team_claims_need_a_team() -> None:
    dominance = Assertion(kind="dominance", subject=HAR, window=H2)
    assert errors(dominance, claim="Đội nhà áp đảo hiệp 2") == []
    assert any("does not name a team" in e for e in errors(dominance, claim="áp đảo hiệp 2"))


def dominance_in(window: MinuteWindow | RollingWindow) -> Assertion:
    return Assertion(kind="dominance", subject=HAR, window=window)


@pytest.mark.parametrize(
    ("claim", "window"),
    [
        ("Momentum nghiêng hẳn về Harbor sau giờ nghỉ", H2),
        ("Harbor áp đảo từ phút 60", MinuteWindow(start_min=60)),
        ("Harbor áp đảo từ phút 60", MinuteWindow(period=2, start_min=60)),
        ("Harbor áp đảo từ phút 60 đến phút 75", MinuteWindow(start_min=60, end_min=75)),
        ("Harbor áp đảo trong 15 phút cuối", RollingWindow.last(15, LAST_MS)),
        ("Harbor áp đảo trong mười lăm phút cuối", RollingWindow.last(15, LAST_MS)),
        ("Harbor dồn ép trong 45 phút cuối", RollingWindow.last(45, LAST_MS)),
        ("Harbor áp đảo tới giờ", MinuteWindow(end_min=96)),  # the clock is 90+6'
        ("Harbor áp đảo", FULL),
    ],
)
def test_windows_from_the_claim_are_grounded(
    claim: str, window: MinuteWindow | RollingWindow
) -> None:
    assert errors(dominance_in(window), claim=claim) == []


@pytest.mark.parametrize(
    ("claim", "window"),
    [  # qwen2.5:3b read "after the break" as "from minute 84", then "from 45" (evaluation h10):
        # without period 2 that takes in the first half's added time; "from 46" misses a minute
        ("Momentum nghiêng hẳn về Harbor sau giờ nghỉ", MinuteWindow(start_min=84)),
        ("Momentum nghiêng hẳn về Harbor sau giờ nghỉ", MinuteWindow(start_min=45)),
        ("Momentum nghiêng hẳn về Harbor sau giờ nghỉ", MinuteWindow(start_min=46)),
        ("Momentum nghiêng hẳn về Harbor sau giờ nghỉ", H1),
        ("Momentum nghiêng hẳn về Harbor sau giờ nghỉ", FULL),  # then the whole match
        ("Harbor áp đảo từ phút 60", MinuteWindow(start_min=65)),
        ("Harbor áp đảo trong 15 phút cuối", RollingWindow.last(20, LAST_MS)),
        ("Harbor áp đảo", H2),  # no time words: a half is invented
    ],
)
def test_windows_the_claim_never_names_are_rejected(
    claim: str, window: MinuteWindow | RollingWindow
) -> None:
    assert any("is not what the claim says" in e for e in errors(dominance_in(window), claim=claim))


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


def test_the_whole_match_fits_a_clause_beside_a_timed_one() -> None:
    won = Assertion(kind="value", subject=HAR, metric="score", op=">", value=0, window=FULL)
    dominated = dominance_in(H2)
    assert errors(dominated, won, claim="HAR áp đảo hiệp 2 và thắng trận") == []
    assert errors(dominance_in(FULL), won, claim="HAR áp đảo hiệp 2 và thắng trận") != []
