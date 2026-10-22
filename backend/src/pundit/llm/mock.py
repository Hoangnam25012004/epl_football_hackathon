"""Mock LLM (docs/day2-design.md section 8.2; Day 3 section 13.4): the rule analyst and the
templates behind the LLM ports, plus deterministic fault injection to exercise every recovery
path.

The same code is the fallback when a real model fails (principle L7), so the most-tested path
is the one the system falls back to.
"""

import asyncio
from collections.abc import Collection, Sequence

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.commentary import render_version
from pundit.agents.contracts import (
    Assertion,
    ClaimContext,
    ClaimSpec,
    CommentaryDraft,
    ExplanationDraft,
    FactPack,
    GraphicsChoice,
    GraphicsOptions,
    GuardrailViolation,
    JudgedClaim,
    Lang,
    MatchContext,
    PlayerRef,
    RecapDraft,
    Register,
    Subject,
    TalkingDraft,
    Trigger,
)
from pundit.agents.explain_templates import render
from pundit.agents.glossary import OUTCOME_EN, OUTCOME_VI, forbidden_categories
from pundit.agents.graphics import rule_choice
from pundit.agents.guardrail import rounding_match
from pundit.agents.language import Text
from pundit.agents.numbers import extract_numbers
from pundit.agents.recap import RecapPack, render_recap
from pundit.agents.studio_guard import scope_of
from pundit.agents.talking_points import render_lines
from pundit.llm.faults import FaultCounter, FaultKind, FaultPlan, Role
from pundit.llm.ports import LlmError, LlmOutputError

MOCK_MODEL = "mock"
MOCK_HANG_S = 3600.0  # a "timeout" fault sleeps until the orchestrator's timeout cancels it
UNSAFE_SUFFIX = {"vi": " Thật là vô dụng.", "en": " Simply useless."}
INVALID_JSON = '{"language": "vi", "assertions": "not-a-list"'


async def _misbehave(fault: FaultKind | None, role: str) -> None:
    if fault == "timeout":
        await asyncio.sleep(MOCK_HANG_S)
    if fault == "error":
        raise LlmError(f"mock {role}: injected provider error")
    if fault == "invalid_json":
        raise LlmOutputError(
            f"mock {role}: injected invalid JSON",
            errors=["the output is not valid JSON for the schema"],
            raw=INVALID_JSON,
        )


def _swap(subject: Subject | None, match: MatchContext) -> Subject | None:
    if subject is None or subject.kind != "team" or subject.team is None:
        return subject
    return Subject(kind="team", team=match.opponent(subject.team))


def swap_teams(spec: ClaimSpec, match: MatchContext) -> ClaimSpec:
    """The `wrong_entity` fault: every team subject replaced by its opponent."""
    swapped: list[Assertion] = []
    for a in spec.assertions:
        subject = _swap(a.subject, match)
        assert subject is not None
        swapped.append(a.model_copy(update={"subject": subject, "other": _swap(a.other, match)}))
    return spec.model_copy(update={"assertions": tuple(swapped)})


def hallucinate(text: str, judged: JudgedClaim) -> str:
    """The `hallucinate` fault: one number replaced by a value that matches no fact."""
    return bump_number(text, [f.value for f in judged.all_facts()], judged.language)


def bump_number(text: str, values: Sequence[float], language: Lang) -> str:
    """One number of `text` moved to a value that matches none of `values` (or, without a
    number to move, a sentence with an invented count added)."""
    claim = Text(text)
    for number in extract_numbers(claim):
        if number.minute or number.period or number.pair is not None:
            continue
        unit = (
            1.0 if number.decimals == 0 else 10.0 ** (1 - number.decimals)
        )  # 61 -> 65, 2.24 -> 2.64
        for bump in (4, 7, 11, 23, 37, 51):
            candidate = number.signed + bump * unit
            if not any(rounding_match(candidate, number.decimals, v) for v in values):
                written = f"{candidate:.{number.decimals}f}"
                start = number.start - (1 if number.negative else 0)
                return text[:start] + written + text[number.end :]
    extra = 97.0
    while any(rounding_match(extra, 0, v) for v in values):
        extra += 1
    sentence = (
        f" Có tổng cộng {extra:.0f} cú sút."
        if language == "vi"
        else f" There were {extra:.0f} shots."
    )
    return text + sentence


class MockClaimAnalyst:
    """ClaimAnalystPort: the rule analyst, with optional injected faults."""

    model = MOCK_MODEL

    def __init__(self, faults: FaultCounter | None = None, *, latency_s: float = 0.0) -> None:
        self._faults = faults or FaultPlan().counter()
        self._latency_s = latency_s

    async def analyze(self, ctx: ClaimContext, feedback: list[str] | None = None) -> ClaimSpec:
        del feedback  # the rules do not learn from feedback
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        fault = self._faults.take("analyst")
        await _misbehave(fault, "analyst")
        spec = analyze_claim(ctx)
        if fault == "wrong_entity":
            spec = swap_teams(spec, ctx.match)
        return spec


class MockExplainer:
    """ExplainerPort: the templates, with optional injected faults."""

    model = MOCK_MODEL

    def __init__(self, faults: FaultCounter | None = None, *, latency_s: float = 0.0) -> None:
        self._faults = faults or FaultPlan().counter()
        self._latency_s = latency_s

    async def explain(
        self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
    ) -> ExplanationDraft:
        del feedback
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        fault = self._faults.take("explainer")
        await _misbehave(fault, "explainer")
        text = render(judged)
        if fault == "hallucinate":
            text = hallucinate(text, judged)
        elif fault == "unsafe":
            text += UNSAFE_SUFFIX[judged.language]
        return ExplanationDraft(text=text, language=judged.language, source="llm", attempt=1)


# --------------------------------------------------------------------------- studio (Day 3)


def _unique_stranger(match: MatchContext, allowed: Collection[str]) -> PlayerRef | None:
    """The first player of the squads who is not part of the moment and whose short name
    belongs to nobody else (so the Guardrail cannot read it as an allowed player)."""
    names = [p.short_name.casefold() for p in match.players]
    for player in match.players:
        if player.id not in allowed and names.count(player.short_name.casefold()) == 1:
            return player
    return None


def wrong_player(text: str, match: MatchContext, allowed: Collection[str]) -> str:
    """The `wrong_player` fault: the text names a player who is not part of the moment."""
    stranger = _unique_stranger(match, allowed)
    if stranger is None:
        return text
    for player_id in sorted(allowed):
        name = match.player(player_id).short_name
        if name in text:
            return text.replace(name, stranger.short_name, 1)
    return f"{stranger.short_name}: {text}"


def wrong_outcome(text: str, trigger: Trigger, language: Lang) -> str:
    """The `wrong_outcome` fault: an outcome word that contradicts what happened ("scores" for a
    saved shot). Moments without an outcome to contradict are left alone."""
    categories = sorted(forbidden_categories(trigger), key=lambda c: (c != "goal", c))
    if not categories:
        return text
    word = (OUTCOME_VI if language == "vi" else OUTCOME_EN)[categories[0]][0]
    return f"{text.rstrip().rstrip('.!')}, {word}."


def _corrupt(text: str, fault: FaultKind | None, pack: FactPack, language: Lang) -> str:
    if fault == "hallucinate":
        return bump_number(text, [f.value for f in pack.facts], language)
    if fault == "unsafe":
        return text + UNSAFE_SUFFIX[language]
    if fault == "wrong_player":
        return wrong_player(text, pack.match, scope_of(pack).players)
    if fault == "wrong_outcome":
        return wrong_outcome(text, pack.trigger, language)
    return text


def _other(language: Lang) -> Lang:
    return "en" if language == "vi" else "vi"


class _MockStudioRole:
    model = MOCK_MODEL

    def __init__(self, faults: FaultCounter | None = None, *, latency_s: float = 0.0) -> None:
        self._faults = faults or FaultPlan().counter()
        self._latency_s = latency_s

    async def _fault(self, role: Role) -> FaultKind | None:
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        fault = self._faults.take(role)
        await _misbehave(fault, role)
        return fault


class MockTalkingPointWriter(_MockStudioRole):
    """TalkingPointWriterPort: the talking-point templates of both languages."""

    async def write(
        self, pack: FactPack, feedback: list[GuardrailViolation] | None = None
    ) -> TalkingDraft:
        del feedback
        fault = await self._fault("talker")
        en = [line.text for line in render_lines(pack, "en")]
        vi = [line.text for line in render_lines(pack, "vi")]
        if fault == "wrong_language":
            en, vi = vi, en
        else:
            en[0], vi[0] = _corrupt(en[0], fault, pack, "en"), _corrupt(vi[0], fault, pack, "vi")
        return TalkingDraft(en=tuple(en), vi=tuple(vi))


class MockCommentator(_MockStudioRole):
    """CommentatorPort: the commentary templates of one language, both registers."""

    async def comment(
        self, pack: FactPack, language: Lang, feedback: list[GuardrailViolation] | None = None
    ) -> CommentaryDraft:
        del feedback
        fault = await self._fault("commentator")
        written = _other(language) if fault == "wrong_language" else language
        analyst = _corrupt(render_version(pack, written, "analyst"), fault, pack, language)
        casual = _corrupt(render_version(pack, written, "casual"), fault, pack, language)
        return CommentaryDraft(analyst=analyst, casual=casual)


class MockGraphicsDesigner(_MockStudioRole):
    """GraphicsDesignerPort: the rule table's choice."""

    async def choose(self, pack: FactPack, options: GraphicsOptions) -> GraphicsChoice:
        del options
        fault = await self._fault("designer")
        choice = rule_choice(pack)
        if choice is None:
            raise LlmOutputError("mock designer: nothing to choose", errors=["no candidates"])
        if fault == "bad_choice":
            return choice.model_copy(update={"stat_keys": ("invented_stat",)})
        return choice


class MockRecapWriter(_MockStudioRole):
    """RecapWriterPort: the recap template of one language and register."""

    async def write(
        self,
        pack: RecapPack,
        language: Lang,
        mode: Register,
        feedback: list[GuardrailViolation] | None = None,
    ) -> RecapDraft:
        del feedback
        fault = await self._fault("recapper")
        written = _other(language) if fault == "wrong_language" else language
        text = render_recap(pack, written, mode)
        paragraphs = list(text.paragraphs)
        if fault == "hallucinate":
            paragraphs[0] = bump_number(paragraphs[0], [f.value for f in pack.facts], language)
        elif fault == "unsafe":
            paragraphs[-1] += UNSAFE_SUFFIX[language]
        elif fault == "wrong_player":
            paragraphs[0] = wrong_player(paragraphs[0], pack.match, pack.players)
        return RecapDraft(title=text.title, paragraphs=tuple(paragraphs))
