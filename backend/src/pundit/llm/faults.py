"""Fault injection for the mock LLM (docs/day2-design.md section 8.2).

`MOCK_LLM_FAULTS=analyst:timeout:1,explainer:hallucinate:2` means: in every claim, the first
analyst call times out and the first two explainer calls hallucinate a number. Each claim starts
from a fresh counter, so a demo shows the same recovery on every claim and tests do not depend
on the order of claims.
"""

from dataclasses import dataclass
from typing import Literal, get_args

Role = Literal["analyst", "explainer", "talker", "commentator", "designer", "recapper"]
FaultKind = Literal[
    "timeout",
    "error",
    "invalid_json",
    "wrong_entity",
    "hallucinate",
    "unsafe",
    "wrong_player",  # studio: a player of the squad who is not part of the moment
    "wrong_outcome",  # studio: "scores" for a shot that was saved
    "wrong_language",  # studio: English text where Vietnamese was asked
    "bad_choice",  # graphics designer: a stat that is not among the candidates
]

ROLES: tuple[Role, ...] = get_args(Role)
KINDS: tuple[FaultKind, ...] = get_args(FaultKind)
_WRITER_FAULTS: frozenset[FaultKind] = frozenset(
    {"timeout", "error", "invalid_json", "hallucinate", "unsafe", "wrong_player",
     "wrong_outcome", "wrong_language"}
)  # fmt: skip
_RECAP_FAULTS: frozenset[FaultKind] = frozenset(
    {"timeout", "error", "invalid_json", "hallucinate", "unsafe", "wrong_player",
     "wrong_language"}
)  # fmt: skip
ROLE_FAULTS: dict[Role, frozenset[FaultKind]] = {
    "analyst": frozenset({"timeout", "error", "invalid_json", "wrong_entity"}),
    "explainer": frozenset({"timeout", "error", "invalid_json", "hallucinate", "unsafe"}),
    "talker": _WRITER_FAULTS,  # Day 3 talking points
    "commentator": _WRITER_FAULTS,  # Day 3 commentary
    "designer": frozenset({"timeout", "error", "invalid_json", "bad_choice"}),
    "recapper": _RECAP_FAULTS,
}


@dataclass(frozen=True)
class Fault:
    role: Role
    kind: FaultKind
    count: int


def _role(text: str, item: str) -> Role:
    for role in ROLES:
        if role == text:
            return role
    raise ValueError(f"{item!r}: role must be one of {', '.join(ROLES)}")


def _kind(text: str, role: Role, item: str) -> FaultKind:
    for kind in KINDS:
        if kind == text and kind in ROLE_FAULTS[role]:
            return kind
    allowed = ", ".join(sorted(ROLE_FAULTS[role]))
    raise ValueError(f"{item!r}: {role} faults are {allowed}")


@dataclass(frozen=True)
class FaultPlan:
    faults: tuple[Fault, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "FaultPlan":
        faults: list[Fault] = []
        for item in (part.strip() for part in text.split(",")):
            if not item:
                continue
            pieces = [p.strip() for p in item.split(":")]
            if len(pieces) not in (2, 3):
                raise ValueError(f"{item!r}: use role:kind[:count], e.g. explainer:hallucinate:2")
            role = _role(pieces[0], item)
            kind = _kind(pieces[1], role, item)
            count = 1
            if len(pieces) == 3:
                if not pieces[2].isdigit() or int(pieces[2]) < 1:
                    raise ValueError(f"{item!r}: count must be a whole number >= 1")
                count = int(pieces[2])
            faults.append(Fault(role, kind, count))
        return cls(tuple(faults))

    def counter(self) -> "FaultCounter":
        return FaultCounter(self)


@dataclass
class _Left:
    kind: FaultKind
    count: int


class FaultCounter:
    """The faults left for one claim; `take` consumes the fault of one call (or None)."""

    def __init__(self, plan: FaultPlan) -> None:
        self._left: dict[Role, list[_Left]] = {role: [] for role in ROLES}
        for fault in plan.faults:
            self._left[fault.role].append(_Left(fault.kind, fault.count))

    def take(self, role: Role) -> FaultKind | None:
        queue = self._left[role]
        while queue and queue[0].count == 0:
            queue.pop(0)
        if not queue:
            return None
        queue[0].count -= 1
        return queue[0].kind


# Day 5: faults of the mock speech synthesizer (docs/day5-design.md section 8.2)
TtsFaultKind = Literal["error", "timeout", "throttle", "bad_audio", "auth"]
TTS_KINDS: tuple[TtsFaultKind, ...] = get_args(TtsFaultKind)


@dataclass(frozen=True)
class TtsFault:
    kind: TtsFaultKind
    count: int | None  # None: every call


@dataclass(frozen=True)
class TtsFaultPlan:
    """`MOCK_TTS_FAULTS=timeout:2,error` means: the next two synthesis calls time out, then
    every call fails. Unlike the LLM plan the counters are global to the synthesizer: speech is
    not grouped by claim."""

    faults: tuple[TtsFault, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "TtsFaultPlan":
        faults: list[TtsFault] = []
        for item in (part.strip() for part in text.split(",")):
            if not item:
                continue
            pieces = [p.strip() for p in item.split(":")]
            if len(pieces) not in (1, 2) or pieces[0] not in TTS_KINDS:
                raise ValueError(f"{item!r}: use kind[:count] with kind in {', '.join(TTS_KINDS)}")
            count: int | None = None
            if len(pieces) == 2:
                if not pieces[1].isdigit() or int(pieces[1]) < 1:
                    raise ValueError(f"{item!r}: count must be a whole number >= 1")
                count = int(pieces[1])
            kind = next(k for k in TTS_KINDS if k == pieces[0])
            faults.append(TtsFault(kind, count))
        return cls(tuple(faults))


@dataclass
class _TtsLeft:
    kind: TtsFaultKind
    count: int | None


class TtsFaultCounter:
    """The faults left; `take` consumes the fault of one call (or None)."""

    def __init__(self, plan: TtsFaultPlan) -> None:
        self._left = [_TtsLeft(f.kind, f.count) for f in plan.faults]

    def take(self) -> TtsFaultKind | None:
        while self._left and self._left[0].count == 0:
            self._left.pop(0)
        if not self._left:
            return None
        entry = self._left[0]
        if entry.count is not None:
            entry.count -= 1
        return entry.kind
