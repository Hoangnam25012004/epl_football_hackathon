"""Pitch geometry and the attacking-frame convention (docs/day1-design.md section 3.1).

Every event's (x, y) is expressed in the frame of the team performing it: that team always
attacks towards x = 105. `flip` converts a point into the other team's frame.
"""

import math

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
CENTER = (52.5, 34.0)
HALFWAY_X = 52.5

GOAL_X = 105.0
GOAL_Y = 34.0
GOAL_WIDTH = 7.32

BOX_X_MIN = 88.5
BOX_Y_MIN = 13.84
BOX_Y_MAX = 54.16
SIX_YARD_X_MIN = 99.5
PENALTY_SPOT = (94.0, 34.0)

FINAL_THIRD_X = 70.0
PPDA_ZONE_X = 42.0  # start of the pressing team's attacking 60%, in its own frame
PPDA_OPPONENT_MAX_X = PITCH_LENGTH - PPDA_ZONE_X  # opponent's defensive 60%, in its own frame


def flip(x: float, y: float) -> tuple[float, float]:
    """Rotate a point by 180 degrees: the same spot seen from the other team's frame."""
    return PITCH_LENGTH - x, PITCH_WIDTH - y


def in_pitch(x: float, y: float) -> bool:
    return 0.0 <= x <= PITCH_LENGTH and 0.0 <= y <= PITCH_WIDTH


def in_box(x: float, y: float) -> bool:
    """Inside the penalty area the team is attacking."""
    return x >= BOX_X_MIN and BOX_Y_MIN <= y <= BOX_Y_MAX


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def distance_to_goal(x: float, y: float) -> float:
    return math.hypot(GOAL_X - x, GOAL_Y - y)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_coord(value: float) -> float:
    """Coordinates are stored with 0.1 m resolution so serialized data is stable."""
    return round(value, 1)


def attacks_right(*, is_home: bool, period: int, home_attacks_right_p1: bool) -> bool:
    """Whether a team attacks towards the right-hand goal of the broadcast picture."""
    home_right = home_attacks_right_p1 if period == 1 else not home_attacks_right_p1
    return home_right if is_home else not home_right


def to_broadcast(
    x: float, y: float, *, is_home: bool, period: int, home_attacks_right_p1: bool
) -> tuple[float, float]:
    """Convert a point from a team's attacking frame to the fixed broadcast frame."""
    if attacks_right(is_home=is_home, period=period, home_attacks_right_p1=home_attacks_right_p1):
        return x, y
    return flip(x, y)
