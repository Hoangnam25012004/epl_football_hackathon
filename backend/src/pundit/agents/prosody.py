"""Prosody presets: how a line is read (docs/day5-design.md section 6).

Vietnamese voices have no `emphasis` element (only three en-US voices do) and no speaking
styles, so stress comes from rate, pitch, volume and pauses. A preset is data: the SSML of the
server and the browser's voice both read it (rate / pitch / volume as factors, 1.0 = the voice's
own). Volume is 100 by default in Azure, the loudest: ordinary lines are made quieter so that a
goal stands out.
"""

from dataclasses import dataclass

from pundit.agents.contracts import ProsodyPreset


@dataclass(frozen=True)
class Prosody:
    rate_pct: int
    pitch_pct: int
    volume_pct: int

    @property
    def rate(self) -> float:
        return round(1 + self.rate_pct / 100, 4)

    @property
    def pitch(self) -> float:
        return round(1 + self.pitch_pct / 100, 4)

    @property
    def volume(self) -> float:
        return round(min(1.0, 1 + self.volume_pct / 100), 4)


PRESETS: dict[ProsodyPreset, Prosody] = {
    "calm": Prosody(0, 0, -15),  # stat ticks, added time, start / end of a half
    "lively": Prosody(6, 4, -8),  # dangerous spell, momentum shift, rocket shot
    "excited": Prosody(12, 8, 0),  # big chance, red card, an ordinary goal
    "peak": Prosody(16, 12, 0),  # equaliser, go-ahead goal, a goal from the 80th minute
    "burst": Prosody(20, 18, 0),  # the opening exclamation of an excited line: "Vào!"
    "tension": Prosody(-4, -3, 0),  # VAR
    "explainer": Prosody(-6, 0, -10),  # "new to football": the rule, a little slower
    "recap": Prosody(-2, 0, -10),
    "greeting": Prosody(0, 2, -10),
}
KIND_PRESETS: dict[str, ProsodyPreset] = {"goal_overturned": "tension", "var_review": "tension"}
BY_EXCITEMENT: tuple[ProsodyPreset, ...] = ("calm", "lively", "excited", "peak")
CALMER: dict[ProsodyPreset, ProsodyPreset] = {"peak": "excited", "excited": "lively"}
BURSTING: frozenset[ProsodyPreset] = frozenset({"excited", "peak"})
MAX_BURST_WORDS = 4
PAUSE_AFTER_FRAMING_MS = 150
PAUSE_AFTER_BURST_MS = 250
PAUSE_BEFORE_EXPLAINER_MS = 600
PAUSE_BETWEEN_PARAGRAPHS_MS = 700
PAUSE_BETWEEN_MOMENTS_MS = 400


def preset_for(kind: str, excitement: int) -> ProsodyPreset:
    """The preset of a trigger kind: VAR has its own; otherwise the excitement of Day 3."""
    if kind in KIND_PRESETS:
        return KIND_PRESETS[kind]
    return BY_EXCITEMENT[max(0, min(3, excitement))]


def calmer(preset: ProsodyPreset) -> ProsodyPreset:
    """One step down (a recap tells a moment again, it is not live)."""
    return CALMER.get(preset, preset)
