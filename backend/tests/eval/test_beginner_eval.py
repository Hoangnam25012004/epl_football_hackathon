"""Beginner mode eval (docs/day6-design.md 4.9, W9). A beginner follows whole matches: every
rule is explained in full once, at the first line that needs it; penalties, red cards and
second yellows are reminded in short afterwards; each explained rule matches an event of its
trigger (an independent table here, not the trigger's own rules); the explanation is in the
viewer's language; a viewer who is not a beginner gets none.

The four cached matches of the studio tests run in CI; four scenarios x three seeds with
`pytest -m slow`."""

from collections import Counter
from collections.abc import Callable

import pytest
from helpers import generated
from studio_helpers import SAMPLES, offline

from pundit.agents.context import build_match_context
from pundit.agents.contracts import Profile, RuleId, RuleView, StudioOutput, Trigger
from pundit.agents.personalize import render_commentary
from pundit.agents.rules_library import REPEAT_SHORT, STAT_RULES
from pundit.domain.events import Event
from pundit.orchestration.studio_eval import OfflineRun

# A football rule is explained only on a trigger that holds one of these events
EVENT_OF_RULE: dict[str, Callable[[Event], bool]] = {
    "offside": lambda e: e.type == "offside" or (e.type == "var_check" and e.reason == "offside"),
    "var": lambda e: e.type == "var_check" or (e.type == "offside" and e.via_var),
    "penalty": lambda e: (
        (e.type == "shot" and e.situation == "penalty")
        or (e.type == "var_check" and e.check_type == "penalty")
    ),
    "red_card": lambda e: (
        (e.type == "card" and e.color == "red")
        or (e.type == "var_check" and e.check_type == "red_card")
    ),
    "second_yellow": lambda e: e.type == "card" and e.color == "second_yellow",
    "yellow_card": lambda e: e.type == "card" and e.color == "yellow",
    "corner": lambda e: e.type == "pass" and e.play_type == "corner",
    "substitution": lambda e: e.type == "substitution",
}
BY_KIND: dict[str, RuleId] = {"added_time": "added_time"}  # a moment of its own, no event
BEGINNERS = (
    Profile(mode="casual", language="vi", beginner=True),
    Profile(mode="casual", language="en", beginner=True),
    Profile(mode="analyst", language="vi", beginner=True),
)
SLOW = [(s, seed) for s in ("balanced", "comeback_var", "dominant_loser", "red_card_swing")
        for seed in (1, 7, 42)]  # fmt: skip


Line = tuple[Trigger, StudioOutput, RuleView | None]


def follow(match: OfflineRun, profile: Profile) -> list[Line]:
    """What one viewer is shown, line after line, as the hub renders it."""
    context = build_match_context(match.view_at(match.triggers[-1].seq))
    seen: set[RuleId] = set()
    shown: list[Line] = []
    for trigger, output in zip(match.triggers, match.outputs, strict=True):
        view, rule = render_commentary(output.commentary, profile, context, seen=seen)
        if rule is not None:
            seen.add(rule)
        shown.append((trigger, output, view.explanation))
    return shown


def trigger_events(match: OfflineRun, trigger: Trigger) -> list[Event]:
    events = {e.id: e for e in match.match.events}
    return [events[i] for i in trigger.trigger_event_ids if i in events]


def check(match: OfflineRun) -> Counter[str]:
    """The checks of 4.9 on one match; returns the rules explained in full (for the tally)."""
    explained: Counter[str] = Counter()
    for profile in BEGINNERS:
        full: Counter[str] = Counter()
        for trigger, output, explanation in follow(match, profile):
            if explanation is None:
                continue
            item = output.commentary
            assert explanation.rule in item.rules, (item.id, explanation.rule)
            if explanation.depth == "full":
                full[explanation.rule] += 1
            else:
                assert explanation.rule in REPEAT_SHORT and full[explanation.rule] == 1
            rule = explanation.rule
            if rule in EVENT_OF_RULE:
                held = trigger_events(match, trigger)
                assert any(EVENT_OF_RULE[rule](e) for e in held), (item.id, rule)
            elif rule not in STAT_RULES:  # a statistics rule: the line names its metric
                assert BY_KIND.get(trigger.kind) == rule, (item.id, rule)
            assert _language_of(explanation.text) == profile.language, explanation.text
        assert all(n == 1 for n in full.values()), full  # in full once per viewer
        if profile.mode == "casual" and profile.language == "vi":
            explained = full
    for profile in (Profile(mode="casual", language="vi"), Profile(mode="analyst", language="en")):
        assert all(explanation is None for _, _, explanation in follow(match, profile))
    return explained


def _language_of(text: str) -> str:
    vietnamese = set("ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")
    return "vi" if any(c in vietnamese for c in text.lower()) else "en"


Miss = tuple[str, str, str]  # (line, rule, the rule explained there instead)


def coverage(match: OfflineRun) -> tuple[int, list[Miss]]:
    """Football rules whose event has a line: how many, and those not explained at that first
    line, with the one explained there instead. A missed rule must come at its next line."""
    total, misses = 0, []
    needed: set[str] = set()
    pending: dict[str, str] = {}  # missed rule -> its first line, until it is explained
    for trigger, output, explanation in follow(match, BEGINNERS[0]):
        held = trigger_events(match, trigger)
        if explanation is not None and explanation.depth == "full":
            pending.pop(explanation.rule, None)
        for rule, holds in EVENT_OF_RULE.items():
            if not any(map(holds, held)):
                continue
            assert rule not in pending or pending[rule] == output.commentary.id, (
                f"{rule} missed at {pending.get(rule)} and again at {output.commentary.id}"
            )
            if rule in needed:
                continue
            needed.add(rule)
            total += 1
            if explanation is None or explanation.rule != rule:
                instead = explanation.rule if explanation is not None else "none"
                misses.append((output.commentary.id, rule, instead))
                pending[rule] = output.commentary.id
    assert not pending, f"never explained: {pending}"
    return total, misses


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_a_beginner_follows_a_whole_match(scenario: str, seed: int) -> None:
    match = offline(scenario, seed)
    assert check(match), "a whole match explains some rule"


def test_rules_are_explained_where_they_first_happen() -> None:
    """Measured on 23/10: 18 football rules met over the four matches, 17 explained at their
    first line. The one other: a goal ruled out by VAR for offside (comeback_var-7, 36') gets
    "offside" (first in RULE_PRIORITY); "var" comes with the next line, the offside itself, at
    the same minute. One rule per line is by design (Day 3): a miss is only ever that."""
    total, misses = 0, []
    for scenario, seed in SAMPLES:
        n, m = coverage(offline(scenario, seed))
        total, misses = total + n, misses + m
    assert (total - len(misses)) / total >= 0.94, misses
    for line, rule, instead in misses:
        assert instead in EVENT_OF_RULE, (line, rule)  # another football rule took the line


@pytest.mark.slow
@pytest.mark.parametrize(("scenario", "seed"), SLOW)
def test_beginners_over_four_scenarios_and_three_seeds(scenario: str, seed: int) -> None:
    from studio_helpers import offline as cached

    check(cached(scenario, seed) if (scenario, seed) in SAMPLES else _offline(scenario, seed))


def _offline(scenario: str, seed: int) -> OfflineRun:
    from helpers import run

    from pundit.llm.settings import Settings
    from pundit.orchestration.recap_service import RecapService
    from pundit.orchestration.studio_eval import run_offline
    from pundit.orchestration.studio_service import StudioService

    settings = Settings()
    service = StudioService(settings, offline=True)
    return run(run_offline(generated(scenario, seed), service, RecapService(settings)))
