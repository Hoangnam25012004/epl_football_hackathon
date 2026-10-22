"""'Người mới xem' library (docs/day3-design.md section 9): static texts, both languages,
lengths, no banned words, no numbers of a match, definitions tied to the stats registry."""

import re
from typing import get_args

from pundit.agents.contracts import RuleId
from pundit.agents.guardrail import UNSAFE_TERMS
from pundit.agents.language import Text, find_terms
from pundit.agents.rules_library import (
    DIGITS_ALLOWED,
    REPEAT_SHORT,
    RULE_PRIORITY,
    RULES,
    STAT_RULES,
    STAT_TERMS,
    mentioned_rules,
    ordered,
    rule_view,
)
from pundit.stats import REGISTRY


def test_every_rule_has_both_languages_and_fits() -> None:
    assert set(RULES) == set(get_args(RuleId))
    for rule, text in RULES.items():
        for language in ("vi", "en"):
            short = rule_view(rule, language, "short")
            full = rule_view(rule, language, "full")
            assert short.title and short.text and full.text, (rule, language)
            assert len(short.text) <= 140, (rule, language, len(short.text))
            assert len(full.text) <= 420, (rule, language, len(full.text))
            assert short.depth == "short" and full.depth == "full"
        del text


def test_no_banned_word_and_no_number_of_a_match() -> None:
    for rule in RULES:
        for language in ("vi", "en"):
            for depth in ("short", "full"):
                text = rule_view(rule, language, depth).text
                banned = find_terms(Text.of(text), UNSAFE_TERMS)  # whole words, like the Guardrail
                assert not banned, (rule, text, banned)
                digits = set(re.findall(r"\d+", text))
                assert digits <= DIGITS_ALLOWED.get(rule, frozenset()), (rule, digits)


def test_statistics_rules_match_the_registry() -> None:
    for rule, metric in STAT_RULES.items():
        assert metric in REGISTRY, (rule, metric)
    assert set(STAT_TERMS) == set(STAT_RULES)


def test_priority_and_repeats() -> None:
    assert set(RULE_PRIORITY) == set(RULES)
    assert ordered(["xg", "red_card", "offside"]) == ["offside", "red_card", "xg"]
    assert {"penalty", "red_card", "second_yellow"} == REPEAT_SHORT


def test_statistics_rules_only_where_the_text_names_the_metric() -> None:
    rules: tuple[RuleId, ...] = ("offside", "xg", "possession", "chaos")
    assert mentioned_rules(rules, ["Việt vị! Calder đứng sai vị trí."]) == ("offside",)
    assert mentioned_rules(rules, ["Calder dứt điểm (xG 0.31)."]) == ("offside", "xg")
    assert mentioned_rules(rules, ["Harbor cầm bóng 57%; chỉ số hỗn loạn 0.52."]) == (
        "offside",
        "possession",
        "chaos",
    )
    assert mentioned_rules(rules, ["Possession: Harbor 57%."]) == ("offside", "possession")
