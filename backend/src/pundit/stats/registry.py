"""Metric registry: one definition per metric, shared by scenarios, CLI, agents and MCP."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pundit.domain.errors import UnknownMetricError
from pundit.domain.ids import PlayerId, TeamId, team_of_player
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, Window
from pundit.stats.result import StatResult, Unit

Scope = Literal["team", "match", "player"]


class MetricParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team: TeamId | None = None
    player: PlayerId | None = None
    window: Window = FULL


MetricFn = Callable[[LogView, MetricParams], StatResult]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    fn: MetricFn
    unit: Unit
    scope: Scope
    supports_player: bool
    description_en: str
    description_vi: str
    definition: str


REGISTRY: dict[str, MetricSpec] = {}


def metric(
    *,
    name: str,
    unit: Unit,
    definition: str,
    description_en: str,
    description_vi: str,
    scope: Scope = "team",
    supports_player: bool = False,
) -> Callable[[MetricFn], MetricFn]:
    def register(fn: MetricFn) -> MetricFn:
        if name in REGISTRY:
            raise ValueError(f"metric {name!r} registered twice")
        REGISTRY[name] = MetricSpec(
            name=name,
            fn=fn,
            unit=unit,
            scope=scope,
            supports_player=supports_player or scope == "player",
            description_en=description_en,
            description_vi=description_vi,
            definition=definition,
        )
        return fn

    return register


def get_spec(name: str) -> MetricSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise UnknownMetricError(f"unknown metric {name!r}; known: {sorted(REGISTRY)}")
    return spec


def _check_params(view: LogView, spec: MetricSpec, params: MetricParams) -> None:
    meta = view.meta
    if spec.scope == "team" and params.team is None:
        raise ValueError(f"{spec.name} needs a team")
    if spec.scope == "match" and (params.team is not None or params.player is not None):
        raise ValueError(f"{spec.name} is a match-level metric; do not pass team or player")
    if spec.scope == "player" and params.player is None:
        raise ValueError(f"{spec.name} needs a player")
    if params.player is not None and not spec.supports_player:
        raise ValueError(f"{spec.name} does not support a player filter")
    if params.team is not None:
        meta.check_team(params.team)
    if params.player is not None:
        meta.player_info(params.player)
        if params.team is not None and team_of_player(params.player) != params.team:
            raise ValueError(f"player {params.player} does not play for {params.team}")


def compute(view: LogView, name: str, params: MetricParams) -> StatResult:
    spec = get_spec(name)
    _check_params(view, spec, params)
    return spec.fn(view, params)


def make_result(
    view: LogView,
    name: str,
    params: MetricParams,
    *,
    value: float | None,
    evidence: Iterable[str],
    numerator: float | None = None,
    denominator: float | None = None,
    label: str | None = None,
) -> StatResult:
    spec = get_spec(name)
    return StatResult(
        metric=name,
        team=params.team,
        player=params.player,
        window=params.window,
        value=value,
        unit=spec.unit,
        numerator=numerator,
        denominator=denominator,
        evidence_event_ids=tuple(sorted(set(evidence))),
        status="ok" if value is not None else "insufficient_data",
        as_of_seq=view.last_seq,
        definition=spec.definition,
        label=label,
    )


class Comparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    home: StatResult
    away: StatResult
    diff: float | None  # home - away
    ratio: float | None  # home / away; None when undefined


def compare(view: LogView, name: str, window: Window = FULL) -> Comparison:
    home_team, away_team = view.meta.team_ids
    home = compute(view, name, MetricParams(team=home_team, window=window))
    away = compute(view, name, MetricParams(team=away_team, window=window))
    diff = None if home.value is None or away.value is None else home.value - away.value
    ratio = None
    if home.value is not None and away.value is not None and away.value != 0:
        ratio = home.value / away.value
    return Comparison(metric=name, home=home, away=away, diff=diff, ratio=ratio)
