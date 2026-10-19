"""Literal types shared by the event schema, metadata and probability models."""

from typing import Literal

Period = Literal[1, 2]
Side = Literal["home", "away"]

Height = Literal["ground", "low", "high"]
PlayType = Literal["open_play", "kick_off", "free_kick", "corner", "throw_in", "goal_kick"]
PassOutcome = Literal["complete", "incomplete", "out", "offside"]

BodyPart = Literal["right_foot", "left_foot", "head"]
Situation = Literal["open_play", "counter", "set_piece", "corner", "penalty"]
ShotOutcome = Literal["goal", "saved", "off_target", "blocked", "post"]

TackleOutcome = Literal["won", "lost"]
CardColor = Literal["yellow", "second_yellow", "red"]
DeadReason = Literal["out_of_play", "foul", "offside", "goal", "period_end"]

CheckType = Literal["goal", "red_card", "penalty"]
VarDecision = Literal["stands", "overturned"]
VarReason = Literal["offside", "foul", "handball", "none"]

Position = Literal["GK", "DF", "MF", "FW"]
Role = Literal["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
Foot = Literal["right", "left"]
Formation = Literal["4-3-3", "4-2-3-1", "4-4-2", "3-5-2"]
Weather = Literal["clear", "cloudy", "rain", "wind"]
