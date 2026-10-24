"""The independent number audit (docs/day6-design.md 4.5, 10.2): a golden table of what it reads,
what has a source, and the proof that it shares no code with the guardrail."""

import ast
from pathlib import Path

import pytest
from helpers import generated, source, verify

import pundit.orchestration.number_audit as number_audit
from pundit.orchestration.number_audit import Sources, audit_claim, audit_text, extract

EMPTY = Sources(values=frozenset(), minutes=frozenset(), ratios=())


def read(text: str) -> list[tuple[str, float | None]]:
    return [(found.kind, found.value) for found in extract(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("kiểm soát bóng 61%", [("percent", 61.0)]),
        ("possession 61 %", [("percent", 61.0)]),
        ("xG 2,24 so với 0,24", [("number", 2.24), ("number", 0.24)]),
        ("xG 2.24 to 0.24.", [("number", 2.24), ("number", 0.24)]),
        ("Harbor Lions FC 1-2 Kestrel Athletic", [("number", 1.0), ("number", 2.0)]),
        ("15\u20131", [("number", 15.0), ("number", 1.0)]),  # an en dash
        ("15 shots to 1", [("number", 15.0), ("number", 1.0)]),
        ("momentum -0.91", [("number", 0.91)]),
        ("PPDA 5.34", [("number", 5.34)]),
        ("ghi bàn ở phút 63", [("minute", None)]),
        ("scored in the 27'", [("minute", None)]),
        ("at 45+2' he scored", [("minute", None)]),
        ("Before minute 30, Ironvale", [("minute", None)]),
        ("Kestrel scored two goals", [("word", 2.0)]),
        ("three yellow cards", [("word", 3.0)]),
        ("Kestrel ghi hai bàn", [("word", 2.0)]),
        ("ba cú sút", [("word", 3.0)]),
        ("Demirel scored twice", [("word", 2.0)]),
        ("twice as many shots", [("ratio", 2.0)]),
        ("gấp đôi đối thủ", [("ratio", 2.0)]),
        ("dứt điểm gấp 8 lần", [("ratio", 8.0)]),
        ("That is 15 times as many.", [("ratio", 15.0)]),
        ("năm nay không có bàn nào", []),  # "năm" = year, "không" = no: no counted noun
        ("trong hiệp H2, xG và PPDA", []),
        ("one of the best", []),
    ],
)
def test_what_the_audit_reads(text: str, expected: list[tuple[str, float | None]]) -> None:
    assert read(text) == expected


def test_minutes_are_read_as_displays() -> None:
    assert [f.text for f in extract("phút 63, 27' và 45 + 2'")] == ["63'", "27'", "45+2'"]


def test_a_number_needs_a_source() -> None:
    allowed = Sources(values=frozenset({61.0, 39.0, 15.0, 1.0}), minutes=frozenset({"63'"}),
                      ratios=(15.0,))  # fmt: skip
    assert audit_text("kiểm soát bóng 61% so với 39%, dứt điểm 15 lần so với 1", allowed).clean
    bad = audit_text("kiểm soát bóng 62% và ghi bàn ở phút 70", allowed)
    assert [f.text for f in bad.unsourced] == ["62%", "70'"]
    assert audit_text("15 times as many", allowed).clean
    assert [f.text for f in audit_text("twice as many", allowed).unsourced] == ["twice as many"]


def test_a_ratio_word_without_two_facts_is_read_by_hand() -> None:
    audit = audit_text("Harbor had twice as many shots", EMPTY)
    assert audit.unsourced == () and [f.text for f in audit.manual] == ["twice as many"]


def test_a_ratio_within_ten_percent() -> None:
    allowed = Sources(values=frozenset(), minutes=frozenset(), ratios=(16 / 2,))
    assert audit_text("gấp tám lần", allowed).clean
    assert audit_text("eight times as many", allowed).clean
    assert audit_text("gấp 8 lần", allowed).clean
    assert not audit_text("gấp 10 lần", allowed).clean


@pytest.mark.parametrize(
    ("text", "at"),
    [
        ("Đội nhà áp đảo hiệp 2", None),
        ("Harbor had twice as many shots as Kestrel in the second half", None),
        ("HAR áp đảo hiệp 2 nhưng vẫn thua", None),
        ("Kestrel won despite lower xG", None),
        ("HAR kiểm soát bóng khoảng 60% trong hiệp 2", None),
        ("Calder ghi bàn trong hiệp 2", None),
        ("HAR có nhiều cú sút hơn KES", "63'"),
    ],
)
def test_published_explanations_have_a_source_for_every_number(text: str, at: str | None) -> None:
    result = verify(text, at=at)
    view = source().view_at(result.as_of_seq)
    audit = audit_claim(result, view)
    assert audit.numbers and audit.clean, (result.explanation, audit.unsourced)


def test_an_invented_number_is_caught() -> None:
    result = verify("Đội nhà áp đảo hiệp 2")
    view = source().view_at(result.as_of_seq)
    honest = audit_claim(result, view)
    lying = result.model_copy(update={"explanation": result.explanation.replace("61%", "64%")})
    caught = audit_claim(lying, view)
    assert honest.clean and [f.text for f in caught.unsourced] == ["64%"]
    early = result.model_copy(update={"explanation": "Calder ghi bàn ở phút 12."})
    assert [f.text for f in audit_claim(early, view).unsourced] == [
        "12'"
    ]  # not a second-half event


def test_the_minute_of_an_evidence_event_is_a_source() -> None:
    result = verify("Calder ghi bàn trong hiệp 2")
    events = generated("dominant_loser", 42).events
    goal = next(e for e in events if e.type == "goal" and e.team == "harbor_lions")
    said = result.model_copy(
        update={"explanation": f"Tobias Calder ghi bàn ở phút {goal.minute + 1}."}
    )
    assert audit_claim(said, source().view_at(result.as_of_seq)).clean, goal.minute


def test_the_audit_shares_no_code_with_the_guardrail() -> None:
    tree = ast.parse(Path(number_audit.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    banned = {"pundit.agents.numbers", "pundit.agents.guardrail", "pundit.agents.studio_guard",
              "pundit.speech.guard"}  # fmt: skip
    assert not imported & banned


# ------------------------------------------------------------- studio (docs/day6-design.md 4.8)


def test_a_minute_written_in_words_keeps_its_added_time() -> None:
    """ "Phút 45+2:" was read as minute 45 alone."""
    assert [f.text for f in extract("Phút 45+2: cơ hội lớn") if f.kind == "minute"] == ["45+2'"]
    assert [f.text for f in extract("Minute 90+3, a save") if f.kind == "minute"] == ["90+3'"]
    assert [f.text for f in extract("Phút 60: Harbor có 9 cú sút") if f.kind == "minute"] == ["60'"]


def test_the_studio_audit_finds_an_invented_number() -> None:
    from studio_helpers import offline

    from pundit.orchestration.number_audit import audit_studio
    from pundit.orchestration.studio_eval import pack_of

    run = offline("comeback_var", 7)

    def pack_facts(trigger):  # type: ignore[no-untyped-def]
        pack = pack_of(trigger, run.view_at(trigger.seq))
        return list(pack.facts), list(pack.window_numbers)

    pairs = list(zip(run.triggers, run.outputs, strict=True))
    recaps = [(revisions[-1], run.recap_packs[scope]) for scope, revisions in run.recaps.items()]
    clean = audit_studio(pairs, recaps, run.view_at, pack_facts)
    assert clean.texts > 100 and clean.numbers > 100 and clean.unsourced == []
    # one commentary line made to say a number no fact gives
    index = next(i for i, (_, o) in enumerate(pairs) if o.commentary.texts[0].source == "llm")
    trigger, output = pairs[index]
    first = output.commentary.texts[0].model_copy(update={"text": "Ironvale đã có 99 cú sút."})
    item = output.commentary.model_copy(update={"texts": (first, *output.commentary.texts[1:])})
    pairs[index] = (trigger, output.model_copy(update={"commentary": item}))
    doctored = audit_studio(pairs, recaps, run.view_at, pack_facts)
    assert [(i, f.text) for i, f, _ in doctored.unsourced] == [(item.id, "99")]
