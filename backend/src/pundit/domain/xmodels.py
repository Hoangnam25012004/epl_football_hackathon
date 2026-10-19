"""Expected-goals and pass-completion models (docs/day1-design.md section 3.4).

The generator samples outcomes with these models and the stats module measures with them,
so generated data and computed stats are consistent by construction.
"""

import math

from pundit.domain.pitch import GOAL_WIDTH, GOAL_X, GOAL_Y, distance, in_box
from pundit.domain.types import BodyPart, Height, Situation

PENALTY_XG = 0.76
XG_MIN, XG_MAX = 0.01, 0.95


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def shot_angle(x: float, y: float) -> float:
    """Angle (radians) subtended by the goal mouth from (x, y)."""
    dx, dy = GOAL_X - x, abs(GOAL_Y - y)
    return math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2)


def xg(x: float, y: float, body_part: BodyPart, situation: Situation) -> float:
    """Expected goals of a shot, rounded to 3 decimals."""
    if situation == "penalty":
        return PENALTY_XG
    logit = -1.0 + 1.9 * shot_angle(x, y) - 0.11 * math.hypot(GOAL_X - x, GOAL_Y - y)
    if body_part == "head":
        logit -= 0.9
    return round(min(XG_MAX, max(XG_MIN, _sigmoid(logit))), 3)


def pass_completion_prob(
    x: float,
    y: float,
    end_x: float,
    end_y: float,
    *,
    under_pressure: bool,
    height: Height,
    is_cross: bool,
    control: float = 0.5,
) -> float:
    """Probability that a pass is completed. `control` is the team's passing quality (0..1)."""
    length = distance(x, y, end_x, end_y)
    progress = max(0.0, end_x - x)
    into_box = in_box(end_x, end_y) and not in_box(x, y)
    logit = (
        3.2
        - 0.045 * length
        - 0.02 * progress
        - 0.9 * under_pressure
        - 0.6 * into_box
        - 0.5 * (height == "high")
        - 1.2 * is_cross
        + 2.4 * (control - 0.5)
    )
    return min(0.99, max(0.01, _sigmoid(logit)))


def pass_difficulty(
    x: float,
    y: float,
    end_x: float,
    end_y: float,
    *,
    under_pressure: bool,
    height: Height,
    is_cross: bool,
) -> float:
    """Difficulty is a property of the pass itself, so it ignores the team's quality."""
    return 1.0 - pass_completion_prob(
        x, y, end_x, end_y, under_pressure=under_pressure, height=height, is_cross=is_cross
    )


def difficulty_rating(difficulty: float) -> float:
    """Map difficulty (0..1) to a 1..10 rating for display."""
    return round(1 + 9 * difficulty, 1)
