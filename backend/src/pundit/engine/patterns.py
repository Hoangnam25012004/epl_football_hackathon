"""Scripted goal patterns (docs/day1-design.md section 8.5).

Each pattern produces a short, valid chain of events that ends in a goal, so scenario
storylines never depend on random shot outcomes.
"""

from typing import TYPE_CHECKING

from pundit.domain.events import PassEvent, TackleEvent
from pundit.domain.pitch import PITCH_LENGTH, PITCH_WIDTH, clamp
from pundit.domain.types import Height, Role
from pundit.engine.scenarios import ScriptedGoal

if TYPE_CHECKING:
    from pundit.engine.generator import MatchSim


def _ensure_holder(sim: "MatchSim", team: str) -> None:
    """Give `team` the ball with a won tackle if the opponent has it."""
    if sim.holder == team:
        return
    assert sim.carrier is not None
    fx, fy = sim.near_flip(sim.bx, sim.by)
    tackler = sim.pick(team, fx)
    sim.advance(sim.rng.patterns.randint(400, 1_200))
    sim.emit(
        TackleEvent,
        team=team,
        player=tackler,
        x=fx,
        y=fy,
        target_player=sim.carrier,
        outcome="won",
        **sim.new_poss(team),
    )
    sim.carrier = tackler
    sim.bx, sim.by = fx, fy
    sim.possession_started("tackle")


def _ensure_not_holder(sim: "MatchSim", team: str) -> None:
    """Make `team` lose the ball with an intercepted pass if it has it."""
    if sim.holder != team:
        return
    sim.advance(sim.rng.patterns.randint(600, 1_500))
    sim.do_pass(
        team,
        clamp(sim.bx + 12.0, 1.0, 104.0),
        clamp(sim.by + sim.rng.patterns.gauss(0, 8), 1.0, 67.0),
        play_type="open_play",
        height="ground",
        force="incomplete",
    )


def _quick_pass(
    sim: "MatchSim",
    team: str,
    end_x: float,
    end_y: float,
    roles: set[Role],
    *,
    height: Height = "ground",
    is_through: bool = False,
) -> PassEvent:
    assert sim.carrier is not None
    sim.advance(sim.rng.patterns.randint(400, 1_200))
    mate = sim.pick(team, end_x, exclude={sim.carrier}, roles=roles)
    return sim.do_pass(
        team,
        end_x,
        end_y,
        play_type="open_play",
        height=height,
        is_through=is_through,
        force="complete",
        recipient=mate,
    )


def counter_attack(sim: "MatchSim", team: str) -> None:
    rng = sim.rng.patterns
    opp = sim.other(team)
    _ensure_not_holder(sim, team)
    sim.advance(rng.randint(600, 1_500))
    sim.do_pass(
        opp,
        rng.uniform(66, 78),
        rng.uniform(14, 54),
        play_type="open_play",
        height="ground",
        force="incomplete",
        intercept_frac=1.0,
        interceptor_roles={"DM", "CM", "CB"},
    )
    _quick_pass(sim, team, rng.uniform(55, 65), rng.uniform(20, 48), {"AM", "W", "CM"})
    _quick_pass(
        sim, team, rng.uniform(89, 95), rng.uniform(28, 40), {"ST", "W", "AM"}, is_through=True
    )
    sim.advance(rng.randint(300, 900))
    sim.do_shot(team, situation="counter", outcome="goal")


def cutback(sim: "MatchSim", team: str) -> None:
    rng = sim.rng.patterns
    _ensure_holder(sim, team)
    wide_y = rng.uniform(5, 11) if rng.random() < 0.5 else rng.uniform(57, 63)
    _quick_pass(sim, team, rng.uniform(78, 86), wide_y, {"W", "FB"})
    _quick_pass(sim, team, rng.uniform(100, 103), wide_y + rng.uniform(-2, 2), {"FB", "W", "AM"})
    _quick_pass(sim, team, rng.uniform(91, 95), rng.uniform(28, 40), {"ST", "AM", "CM", "W"})
    sim.advance(rng.randint(200, 700))
    sim.do_shot(team, situation="open_play", outcome="goal")


def header_from_corner(sim: "MatchSim", team: str) -> None:
    rng = sim.rng.patterns
    _ensure_holder(sim, team)
    _quick_pass(sim, team, rng.uniform(78, 84), rng.uniform(24, 44), {"AM", "CM", "W"})
    sim.advance(rng.randint(300, 900))
    shot = sim.do_shot(team, situation="open_play", outcome="blocked", resolve=False)
    assert shot.y is not None
    sim.advance(rng.randint(150, 400))
    sim.dead(team, "out_of_play", shot.id)
    sim.advance(rng.randint(25_000, 40_000))
    corner_y = 0.0 if shot.y < 34.0 else PITCH_WIDTH
    taker = sim.pick(team, 100.0, roles={"W", "AM", "CM"})
    sim.carrier = taker
    sim.bx, sim.by = PITCH_LENGTH, corner_y
    sim.possession_started("corner")
    sim.restart = None
    target = sim.pick(team, 97.0, exclude={taker}, roles={"CB", "ST"})
    sim.do_pass(
        team,
        rng.uniform(95, 99),
        rng.uniform(28, 40),
        play_type="corner",
        height="high",
        is_cross=True,
        force="complete",
        recipient=target,
    )
    sim.do_shot(team, body="head", situation="corner", outcome="goal")


def long_shot(sim: "MatchSim", team: str) -> None:
    rng = sim.rng.patterns
    _ensure_holder(sim, team)
    _quick_pass(sim, team, rng.uniform(78, 84), rng.uniform(24, 44), {"AM", "CM"})
    sim.advance(rng.randint(300, 900))
    sim.do_shot(team, situation="open_play", outcome="goal")


PATTERNS = {
    "counter_attack": counter_attack,
    "cutback": cutback,
    "header_from_corner": header_from_corner,
    "long_shot": long_shot,
}


def run_pattern(sim: "MatchSim", goal: ScriptedGoal) -> None:
    team = sim.meta.team_for(goal.side)
    PATTERNS[goal.pattern](sim, team)
    if goal.var_overturn:
        assert sim.last_goal is not None and sim.goal_pass is not None
        sim.var_overturn(sim.last_goal, sim.goal_pass)
