import pytest
from hypothesis import given
from hypothesis import strategies as st

from pundit.domain.ids import event_id, match_id, player_id, team_of_player
from pundit.domain.pitch import attacks_right, flip, in_box, in_pitch, to_broadcast

xs = st.floats(min_value=0.0, max_value=105.0, allow_nan=False)
ys = st.floats(min_value=0.0, max_value=68.0, allow_nan=False)


@given(xs, ys)
def test_flip_is_an_involution(x: float, y: float) -> None:
    fx, fy = flip(*flip(x, y))
    assert fx == pytest.approx(x) and fy == pytest.approx(y)


@given(xs, ys)
def test_flip_stays_on_the_pitch(x: float, y: float) -> None:
    assert in_pitch(*flip(x, y))


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (88.5, 13.84, True),
        (88.49, 34.0, False),
        (100.0, 54.16, True),
        (100.0, 54.17, False),
        (105.0, 34.0, True),
        (52.5, 34.0, False),
    ],
)
def test_box_edges(x: float, y: float, expected: bool) -> None:
    assert in_box(x, y) is expected


def test_teams_swap_ends_at_half_time() -> None:
    assert attacks_right(is_home=True, period=1, home_attacks_right_p1=True)
    assert not attacks_right(is_home=True, period=2, home_attacks_right_p1=True)
    assert not attacks_right(is_home=False, period=1, home_attacks_right_p1=True)
    assert attacks_right(is_home=False, period=2, home_attacks_right_p1=True)
    assert to_broadcast(10.0, 20.0, is_home=True, period=1, home_attacks_right_p1=True) == (
        10.0,
        20.0,
    )
    assert to_broadcast(10.0, 20.0, is_home=True, period=2, home_attacks_right_p1=True) == (
        95.0,
        48.0,
    )


def test_ids_are_deterministic_and_zero_padded() -> None:
    assert player_id("harbor_lions", 9) == "harbor_lions-09"
    assert match_id("dominant_loser", 42) == "dominant_loser-42"
    assert event_id("dominant_loser-42", 17) == "dominant_loser-42-00017"
    assert team_of_player("harbor_lions-09") == "harbor_lions"


@pytest.mark.parametrize("bad", [0, 100, -1])
def test_player_id_rejects_bad_shirts(bad: int) -> None:
    with pytest.raises(ValueError):
        player_id("t", bad)


def test_event_id_rejects_out_of_range_seq() -> None:
    with pytest.raises(ValueError):
        event_id("m-1", 100_000)
    with pytest.raises(ValueError):
        match_id("m", -1)


def test_event_ids_sort_in_seq_order() -> None:
    ids = [event_id("m-1", seq) for seq in (2, 10, 100, 9)]
    assert sorted(ids) == [event_id("m-1", seq) for seq in (2, 9, 10, 100)]
