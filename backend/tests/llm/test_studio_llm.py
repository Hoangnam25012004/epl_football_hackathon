"""Studio LLM layer (docs/day3-design.md section 13): DTOs, prompts, mock writers, faults."""

import json

import pytest
from helpers import run
from studio_helpers import first_trigger, offline, pack

from pundit.agents.commentary import render_version
from pundit.agents.contracts import GraphicsOptions
from pundit.agents.graphics import options, rule_choice
from pundit.agents.recap import render_recap
from pundit.agents.studio_guard import check_studio, scope_of
from pundit.agents.talking_points import render_lines
from pundit.llm.dto import (
    LlmCommentary,
    LlmGraphicsChoice,
    LlmRecap,
    LlmTalkingPoint,
    clean_text,
    to_commentary_draft,
    to_graphics_choice,
    to_recap_draft,
    to_talking_draft,
)
from pundit.llm.faults import ROLE_FAULTS, FaultPlan
from pundit.llm.mock import (
    MockCommentator,
    MockGraphicsDesigner,
    MockRecapWriter,
    MockTalkingPointWriter,
)
from pundit.llm.ports import LlmError, LlmOutputError
from pundit.llm.studio_prompts import (
    COMMENTARY_PROMPT_VERSION,
    RECAP_PROMPT_VERSION,
    TALKING_PROMPT_VERSION,
    commentary_messages,
    graphics_messages,
    recap_messages,
    talking_messages,
)

DL = ("dominant_loser", 42)


def goal_pack():  # type: ignore[no-untyped-def]
    return pack(*DL, first_trigger(*DL, "goal").id)


def saved_pack():  # type: ignore[no-untyped-def]
    return pack(*DL, first_trigger(*DL, "shot", outcome="saved").id)


# --------------------------------------------------------------------------- DTOs


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ('  "Calder   scores!"  ', "Calder scores!"),
        ("- Harbor lead 2-1.", "Harbor lead 2-1."),
        ("“Bàn thắng!”", "Bàn thắng!"),
        ('"He said "no"', '"He said "no"'),  # unbalanced quotes are left alone
        ("xG 0.31", "xG 0.31"),  # numbers are never touched
    ],
)
def test_clean_text(raw: str, clean: str) -> None:
    assert clean_text(raw) == clean


def test_talking_draft_needs_two_lines_per_language() -> None:
    draft = to_talking_draft(
        LlmTalkingPoint(en=["a 1.", "b 2.", "c 3.", "d 4."], vi=["x 1.", " y 2. "])
    )
    assert draft.en == ("a 1.", "b 2.", "c 3.") and draft.vi == ("x 1.", "y 2.")
    with pytest.raises(LlmOutputError) as error:
        to_talking_draft(LlmTalkingPoint(en=["only one."], vi=["", "  "]))
    assert len(error.value.errors) == 2


def test_commentary_recap_and_choice_drafts() -> None:
    assert to_commentary_draft(LlmCommentary(analyst=" A 1. ", casual="B!")).analyst == "A 1."
    with pytest.raises(LlmOutputError):
        to_commentary_draft(LlmCommentary(analyst="", casual="B"))
    recap = to_recap_draft(LlmRecap(title=" T ", paragraphs=["p1", "", "p2", "p3"]))
    assert recap.title == "T" and recap.paragraphs == ("p1", "p2", "p3")
    with pytest.raises(LlmOutputError):
        to_recap_draft(LlmRecap(title="", paragraphs=[]))
    choice = to_graphics_choice(
        LlmGraphicsChoice(template="team_compare", stat_keys=["xg", " xg ", "shots", ""])
    )
    assert choice.stat_keys == ("xg", "shots")


def test_dto_schemas_are_strict() -> None:
    for model in (LlmTalkingPoint, LlmCommentary, LlmGraphicsChoice, LlmRecap):
        schema = model.model_json_schema()
        assert schema.get("additionalProperties") is False, model.__name__
        assert set(schema["required"]) == set(schema["properties"]), model.__name__


# --------------------------------------------------------------------------- prompts


def test_system_prompts_are_static_and_user_prompts_carry_the_pack() -> None:
    goal, saved = goal_pack(), saved_pack()
    system_a, user_a = talking_messages(goal)
    system_b, _ = talking_messages(saved)
    assert system_a == system_b  # prefix cache of a local model
    payload = json.loads(user_a)
    displays = {f["display"] for f in payload["facts_en"]}
    assert {f.display for f in goal.facts} == displays
    assert payload["players"] and payload["score"]
    for language in ("vi", "en"):
        system_1, _ = commentary_messages(goal, language)
        system_2, user_2 = commentary_messages(saved, language)
        assert system_1 == system_2
        assert json.loads(user_2)["forbidden_words"]  # goal words for a saved shot
        assert ("Thuật ngữ" in system_1) == (language == "vi")  # the glossary
    graphics_system, graphics_user = graphics_messages(goal, options(goal))
    assert json.loads(graphics_user)["templates"] and "stat_keys" in graphics_system
    built = offline(*DL).recap_packs["FULL"]
    for mode in ("analyst", "casual"):
        system_recap, user_recap = recap_messages(built, "vi", mode)
        data = json.loads(user_recap)
        assert data["goals"] and data["facts"] and "goals_ruled_out" in data
        assert (
            system_recap
            == recap_messages(offline("balanced", 3).recap_packs["FULL"], "vi", mode)[0]
        )
    assert TALKING_PROMPT_VERSION and COMMENTARY_PROMPT_VERSION and RECAP_PROMPT_VERSION


def test_feedback_reaches_the_prompt() -> None:
    from pundit.agents.contracts import GuardrailViolation

    feedback = [GuardrailViolation(code="UNVERIFIED_NUMBER", detail="99 matches no fact")]
    _, user = commentary_messages(goal_pack(), "vi", feedback)
    assert "99 matches no fact" in json.loads(user)["your_previous_answer_was_rejected"][0]


# --------------------------------------------------------------------------- mock writers


def test_mock_writers_answer_with_the_templates() -> None:
    goal = goal_pack()
    draft = run(MockTalkingPointWriter().write(goal))
    assert draft.en == tuple(line.text for line in render_lines(goal, "en"))
    assert draft.vi == tuple(line.text for line in render_lines(goal, "vi"))
    comment = run(MockCommentator().comment(goal, "vi"))
    assert comment.analyst == render_version(goal, "vi", "analyst")
    assert comment.casual == render_version(goal, "vi", "casual")
    assert run(MockGraphicsDesigner().choose(goal, options(goal))) == rule_choice(goal)
    built = offline(*DL).recap_packs["FULL"]
    recap = run(MockRecapWriter().write(built, "en", "casual"))
    assert recap.paragraphs == render_recap(built, "en", "casual").paragraphs


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        ("hallucinate", "UNVERIFIED_NUMBER"),
        ("unsafe", "UNSAFE_CONTENT"),
        ("wrong_player", "WRONG_ENTITY"),
        ("wrong_outcome", "WRONG_OUTCOME"),
        ("wrong_language", "WRONG_LANGUAGE"),
    ],
)
def test_each_writer_fault_is_caught_by_the_guardrail(fault: str, code: str) -> None:
    for built in (goal_pack(), saved_pack()):
        counter = FaultPlan.parse(f"commentator:{fault}").counter()
        draft = run(MockCommentator(counter).comment(built, "vi"))
        report = check_studio(
            draft.analyst,
            scope_of(built),
            language="vi",
            product="commentary",
            mode="analyst",
            model_text=True,
        )
        assert code in {v.code for v in report.violations}, (built.trigger.kind, draft.analyst)


def test_transport_faults_raise() -> None:
    goal = goal_pack()
    with pytest.raises(LlmError):
        run(MockTalkingPointWriter(FaultPlan.parse("talker:error").counter()).write(goal))
    with pytest.raises(LlmOutputError):
        run(
            MockCommentator(FaultPlan.parse("commentator:invalid_json").counter()).comment(
                goal, "en"
            )
        )
    bad = run(
        MockGraphicsDesigner(FaultPlan.parse("designer:bad_choice").counter()).choose(
            goal, options(goal)
        )
    )
    assert bad.stat_keys == ("invented_stat",)
    empty = GraphicsOptions(templates=(), stat_keys=())
    assert run(MockGraphicsDesigner().choose(goal, empty)) == rule_choice(goal)


def test_faults_are_consumed_per_call() -> None:
    counter = FaultPlan.parse("commentator:hallucinate:1").counter()
    writer = MockCommentator(counter)
    goal = goal_pack()
    first = run(writer.comment(goal, "vi"))
    second = run(writer.comment(goal, "vi"))
    assert first.analyst != second.analyst == render_version(goal, "vi", "analyst")


def test_fault_plan_roles_and_kinds() -> None:
    plan = FaultPlan.parse("commentator:wrong_outcome:2,talker:hallucinate,designer:bad_choice")
    assert [(f.role, f.kind, f.count) for f in plan.faults] == [
        ("commentator", "wrong_outcome", 2),
        ("talker", "hallucinate", 1),
        ("designer", "bad_choice", 1),
    ]
    assert "wrong_outcome" not in ROLE_FAULTS["recapper"]
    for text in ("designer:hallucinate", "recapper:wrong_outcome", "talker:bad_choice"):
        with pytest.raises(ValueError):
            FaultPlan.parse(text)
