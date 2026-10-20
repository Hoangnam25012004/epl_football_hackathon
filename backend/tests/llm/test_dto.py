"""LLM-facing DTO -> ClaimSpec (design section 4.4)."""

from typing import Any

import pytest
from helpers import claim_context

from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow
from pundit.llm.dto import LlmAssertion, LlmClaimSpec, LlmSubject, LlmWindow, dto_to_spec
from pundit.llm.ports import LlmOutputError


def window(
    kind: str, minutes: float | None = None, start: float | None = None, end: float | None = None
) -> LlmWindow:
    return LlmWindow(type=kind, minutes=minutes, start_minute=start, end_minute=end)  # type: ignore[arg-type]


def assertion(**fields: Any) -> LlmAssertion:
    base: dict[str, Any] = {
        "kind": "dominance",
        "subject": LlmSubject(kind="team", id="harbor_lions"),
        "other": None,
        "metric": None,
        "op": None,
        "value": None,
        "ratio": None,
        "window": window("second_half"),
        "negated": False,
        "approx": False,
        "text_span": "HAR",
    }
    base.update(fields)
    return LlmAssertion(**base)


def convert(*assertions: LlmAssertion, reason: str | None = None) -> Any:
    dto = LlmClaimSpec(
        language="en", assertions=list(assertions), unsupported_reason=reason, note=""
    )  # type: ignore[arg-type]
    return dto_to_spec(dto, claim_context("HAR áp đảo hiệp 2"))


@pytest.mark.parametrize(
    ("w", "expected"),
    [
        (window("full"), FULL),
        (window("first_half"), H1),
        (window("second_half"), H2),
        (window("from_minute", start=60), MinuteWindow(start_min=60)),
        (window("until_minute", end=30), MinuteWindow(end_min=30)),
        (window("minute_range", start=60, end=75), MinuteWindow(start_min=60, end_min=75)),
    ],
)
def test_windows(w: LlmWindow, expected: MinuteWindow) -> None:
    assert convert(assertion(window=w)).assertions[0].window == expected


def test_rolling_window_ends_at_as_of() -> None:
    ctx = claim_context("HAR áp đảo 15 phút cuối")
    dto = LlmClaimSpec(
        language="vi",
        assertions=[assertion(window=window("last_minutes", minutes=15))],
        unsupported_reason=None,
        note="",
    )
    converted = dto_to_spec(dto, ctx).assertions[0].window
    assert isinstance(converted, RollingWindow) and converted.end_ms == ctx.match.as_of_ms + 1


@pytest.mark.parametrize(
    "bad",
    [
        window("minute_range", start=75, end=60),
        window("from_minute"),
        window("last_minutes", minutes=0),
    ],
)
def test_bad_windows(bad: LlmWindow) -> None:
    with pytest.raises(LlmOutputError):
        convert(assertion(window=bad))


def test_names_and_short_names_are_forgiven() -> None:
    spec = convert(
        assertion(subject=LlmSubject(kind="team", id="HAR")),
        assertion(
            kind="value",
            subject=LlmSubject(kind="player", id="Tobias Calder"),
            metric="score",
            op=">=",
            value=1,
        ),
    )
    assert spec.assertions[0].subject.team == "harbor_lions"
    assert spec.assertions[1].subject.player == "harbor_lions-09"


def test_errors_are_collected() -> None:
    with pytest.raises(LlmOutputError) as caught:
        convert(
            assertion(subject=LlmSubject(kind="team", id="nope")),
            assertion(kind="value", metric="shots", op="==", value=None),
        )
    assert len(caught.value.errors) == 2


def test_too_many_assertions() -> None:
    with pytest.raises(LlmOutputError):
        convert(*[assertion() for _ in range(4)])


def test_empty_answer_keeps_the_reason_and_the_language_of_the_intake() -> None:
    spec = convert(reason="unknown_entity")
    assert spec.assertions == () and spec.unsupported_reason == "unknown_entity"
    assert spec.language == "vi"  # decided by ClaimIntake, not by the model
    assert convert().unsupported_reason == "unsupported_claim"


def test_dominance_ignores_stray_fields() -> None:
    spec = convert(assertion(metric="shots", op=">", value=3))
    a = spec.assertions[0]
    assert (a.metric, a.op, a.value) == (None, None, None)


@pytest.mark.parametrize(
    ("ratio", "op", "expected"),
    [(None, ">", None), (1.0, ">", None), (0.0, ">", None), (2.0, ">", 2.0), (0.5, "<", 2.0)],
)
def test_ratio_noise_from_small_models(
    ratio: float | None, op: str, expected: float | None
) -> None:
    compare = assertion(
        kind="compare",
        other=LlmSubject(kind="team", id="kestrel_athletic"),
        metric="shots",
        op=op,
        ratio=ratio,
    )
    assert convert(compare).assertions[0].ratio == expected


def test_a_ratio_below_one_with_more_is_still_an_error() -> None:
    compare = assertion(
        kind="compare",
        other=LlmSubject(kind="team", id="kestrel_athletic"),
        metric="shots",
        op=">",
        ratio=0.5,
    )
    spec = convert(compare)
    from helpers import match_context

    from pundit.agents.spec_validation import validate_spec

    assert any("ratio must be > 1" in e for e in validate_spec(spec, match_context()))
