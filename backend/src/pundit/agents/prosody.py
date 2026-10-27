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


# 28/10: a caster's pace, not a reader's: every preset a little faster, wider steps between them,
# and ordinary lines only slightly quieter (the first presets sounded flat on a phone)
PRESETS: dict[ProsodyPreset, Prosody] = {
    "calm": Prosody(8, 2, -5),  # stat ticks, small plays, start / end of a half
    "lively": Prosody(14, 6, -3),  # dangerous spell, momentum shift, rocket shot, box entry
    "excited": Prosody(20, 10, 0),  # big chance, red card, an ordinary goal
    "peak": Prosody(25, 14, 0),  # equaliser, go-ahead goal, a goal from the 80th minute
    "burst": Prosody(30, 20, 0),  # the opening exclamation of an excited line: "Vào!"
    "tension": Prosody(-2, -3, 0),  # VAR
    "explainer": Prosody(-6, 0, -10),  # "new to football": the rule, a little slower
    "recap": Prosody(4, 0, -8),
    "greeting": Prosody(4, 2, -8),
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
PAUSE_BETWEEN_MOMENTS_MS = 250


def preset_for(kind: str, excitement: int) -> ProsodyPreset:
    """The preset of a trigger kind: VAR has its own; otherwise the excitement of Day 3."""
    if kind in KIND_PRESETS:
        return KIND_PRESETS[kind]
    return BY_EXCITEMENT[max(0, min(3, excitement))]


def calmer(preset: ProsodyPreset) -> ProsodyPreset:
    """One step down (a recap tells a moment again, it is not live)."""
    return CALMER.get(preset, preset)
