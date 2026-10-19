import pytest
from hypothesis import given
from hypothesis import strategies as st

from pundit.domain.xmodels import (
    difficulty_rating,
    pass_completion_prob,
    pass_difficulty,
    xg,
)


@pytest.mark.parametrize(
    ("x", "body", "expected"),
    [
        (100.0, "right_foot", 0.70),
        (99.5, "head", 0.43),
        (94.0, "right_foot", 0.27),
        (88.5, "right_foot", 0.12),
        (80.0, "right_foot", 0.04),
    ],
)
def test_reference_values(x: float, body: str, expected: float) -> None:
    assert xg(x, 34.0, body, "open_play") == pytest.approx(expected, abs=0.01)  # type: ignore[arg-type]


def test_penalty_is_fixed() -> None:
    assert xg(94.0, 34.0, "right_foot", "penalty") == 0.76


points = st.tuples(
    st.floats(min_value=0.0, max_value=104.9, allow_nan=False),
    st.floats(min_value=0.0, max_value=68.0, allow_nan=False),
)


@given(points)
def test_xg_is_bounded(point: tuple[float, float]) -> None:
    assert 0.01 <= xg(*point, "right_foot", "open_play") <= 0.95


@given(st.floats(min_value=60.0, max_value=104.0), st.floats(min_value=0.1, max_value=0.9))
def test_closer_on_the_centre_line_is_never_worse(x: float, step: float) -> None:
    assert xg(x, 34.0, "right_foot", "open_play") <= xg(x + step, 34.0, "right_foot", "open_play")


@given(st.floats(min_value=60.0, max_value=104.0), st.floats(min_value=0.0, max_value=34.0))
def test_symmetric_around_the_centre_line(x: float, dy: float) -> None:
    assert xg(x, 34.0 - dy, "left_foot", "open_play") == xg(x, 34.0 + dy, "left_foot", "open_play")


@given(points)
def test_headers_are_never_better_than_kicks(point: tuple[float, float]) -> None:
    assert xg(*point, "head", "open_play") <= xg(*point, "right_foot", "open_play")


@given(st.floats(min_value=3.0, max_value=90.0), st.floats(min_value=0.1, max_value=20.0))
def test_longer_passes_are_harder(length: float, extra: float) -> None:
    def difficulty(end_x: float) -> float:
        return pass_difficulty(
            10.0, 34.0, end_x, 34.0, under_pressure=False, height="ground", is_cross=False
        )

    assert difficulty(10.0 + length) <= difficulty(min(104.0, 10.0 + length + extra))


def test_pressure_and_crosses_make_passes_harder() -> None:
    base = {"height": "ground", "is_cross": False}
    calm = pass_difficulty(40, 34, 55, 34, under_pressure=False, **base)  # type: ignore[arg-type]
    pressed = pass_difficulty(40, 34, 55, 34, under_pressure=True, **base)  # type: ignore[arg-type]
    cross = pass_difficulty(85, 5, 97, 34, under_pressure=False, height="high", is_cross=True)
    assert calm < pressed < cross


def test_control_changes_completion_but_not_difficulty() -> None:
    kwargs = {"under_pressure": False, "height": "ground", "is_cross": False}
    weak = pass_completion_prob(40, 34, 55, 34, control=0.2, **kwargs)  # type: ignore[arg-type]
    strong = pass_completion_prob(40, 34, 55, 34, control=0.8, **kwargs)  # type: ignore[arg-type]
    assert weak < strong
    assert pass_difficulty(40, 34, 55, 34, **kwargs) == pytest.approx(  # type: ignore[arg-type]
        1 - pass_completion_prob(40, 34, 55, 34, **kwargs)  # type: ignore[arg-type]
    )


def test_difficulty_rating_scale() -> None:
    assert difficulty_rating(0.0) == 1.0
    assert difficulty_rating(1.0) == 10.0
    assert difficulty_rating(0.5) == 5.5
