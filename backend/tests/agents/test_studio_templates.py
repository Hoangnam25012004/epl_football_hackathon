"""Templates (docs/day3-design.md sections 6-8 and principle L4): every phrasing that can be
chosen passes the studio Guardrail as model text; at least 3 phrasings per group; the VI and EN
banks are parallel so both versions of an item cite the same numbers (S-5)."""

import pytest
from studio_helpers import MATRIX, SAMPLES, pack, triggers

from pundit.agents.commentary import (
    BANKS,
    VERSIONS,
    bank_key,
    excitement,
    render_texts,
    render_version,
    speak,
)
from pundit.agents.contracts import FactPack
from pundit.agents.graphics import SUBTITLES, TITLES, texts
from pundit.agents.language import Text
from pundit.agents.numbers import extract_numbers
from pundit.agents.phrasebank import (
    capitalize_sentences,
    fields_of,
    fill,
    pick,
    stable_index,
    usable,
)
from pundit.agents.studio_guard import check_studio, scope_of
from pundit.agents.studio_values import pack_values
from pundit.agents.talking_points import TEMPLATES, render_lines

# placeholders that never carry a statistic (names, minutes, windows, words)
TEXTUAL = {
    "team", "opp", "team_s", "opp_s", "home", "away", "home_s", "away_s", "scorer", "assist",
    "keeper", "shooter", "carded", "player_in", "player_out", "taker", "offside_player",
    "minute", "half", "reason", "check", "fate", "last5", "last10", "last15", "tick",
    "tackler", "tackled", "fouler", "fouled", "passer", "receiver",
}  # fmt: skip


def numeric_fields(template: str) -> frozenset[str]:
    return frozenset(name for name in fields_of(template) if name not in TEXTUAL)


def stat_numbers(text: str) -> list[float]:
    found = [n for n in extract_numbers(Text.of(text)) if not n.minute and not n.period]
    return sorted(round(n.signed, 4) for n in found)


def _phrasings(built: FactPack) -> list[tuple[str, str, str, str | None]]:
    """(product, language, text, mode) of every phrasing that can be chosen for the pack."""
    out: list[tuple[str, str, str, str | None]] = []
    trigger = built.trigger
    for language in ("vi", "en"):
        values = pack_values(built, language)
        for mode in ("analyst", "casual"):
            tiers = BANKS.get(bank_key(trigger), {}).get((language, mode), ())
            usable_tiers = [tier for tier in tiers if usable(tier, values)]
            for variant in usable(usable_tiers[0], values) if usable_tiers else []:
                out.append(("commentary", language, fill(variant, values), mode))
        for _, variants in TEMPLATES.get(trigger.kind, {}).get(language, ()):
            for variant in usable(variants, values):
                out.append(("talking_point", language, fill(variant, values), None))
    return out


def _check_every_phrasing(scenario: str, seed: int) -> None:
    failures = []
    for trigger in triggers(scenario, seed):
        built = pack(scenario, seed, trigger.id)
        scope = scope_of(built)
        for product, language, text, mode in _phrasings(built):
            report = check_studio(
                text,
                scope,
                language=language,  # type: ignore[arg-type]
                product=product,  # type: ignore[arg-type]
                mode=mode,  # type: ignore[arg-type]
                model_text=True,
            )
            if not report.passed:
                failures.append((trigger.id, text, [v.code for v in report.violations]))
        words = texts(built)
        for part in ("title", "subtitle"):
            for language in ("vi", "en"):
                text = words[f"{part}_{language}"]
                if text:
                    report = check_studio(
                        text, scope, language=language, product="graphic", part=part
                    )
                    if not report.passed:
                        failures.append((trigger.id, text, [v.code for v in report.violations]))
    assert not failures, failures[:5]


def test_every_line_is_read_and_near_misses_are_livelier() -> None:
    """28/10: the voice reads every line, not only the big moments; a saved shot or one off the
    post is read livelier than one that goes wide."""
    found = triggers("dominant_loser", 42)
    assert all(speak(pack("dominant_loser", 42, t.id)) for t in found)
    shots = [t for t in found if t.kind == "shot"]
    by_outcome = {t.outcome: excitement(pack("dominant_loser", 42, t.id)) for t in shots}
    assert by_outcome.get("saved", 1) == 1 and by_outcome.get("off_target", 0) == 0
    small = [t for t in found if t.kind == "box_entry"]
    assert small and excitement(pack("dominant_loser", 42, small[0].id)) == 1


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_every_phrasing_passes_the_guardrail(scenario: str, seed: int) -> None:
    _check_every_phrasing(scenario, seed)


@pytest.mark.slow
@pytest.mark.parametrize(("scenario", "seed"), MATRIX)
def test_every_phrasing_passes_on_the_matrix(scenario: str, seed: int) -> None:
    _check_every_phrasing(scenario, seed)


def test_at_least_three_phrasings_per_group() -> None:
    for key, versions in BANKS.items():
        for version, tiers in versions.items():
            assert len(tiers[0]) >= 3, (key, version)
    for kind, by_language in TEMPLATES.items():
        for language, lines in by_language.items():
            for angle, variants in lines:
                assert len(variants) >= 3, (kind, language, angle)


def test_vi_and_en_banks_are_parallel() -> None:
    """Same tiers, same number of phrasings, same statistics at the same index: with a choice
    that does not depend on the language, both versions cite the same numbers."""
    for key, versions in BANKS.items():
        for mode in ("analyst", "casual"):
            vi, en = versions[("vi", mode)], versions[("en", mode)]
            assert [len(t) for t in vi] == [len(t) for t in en], (key, mode)
            for tier_vi, tier_en in zip(vi, en, strict=True):
                for a, b in zip(tier_vi, tier_en, strict=True):
                    assert numeric_fields(a) == numeric_fields(b), (key, mode, a, b)
    for kind, by_language in TEMPLATES.items():
        vi, en = by_language["vi"], by_language["en"]
        assert [angle for angle, _ in vi] == [angle for angle, _ in en], kind
        for (_, variants_vi), (_, variants_en) in zip(vi, en, strict=True):
            assert len(variants_vi) == len(variants_en), kind
            for a, b in zip(variants_vi, variants_en, strict=True):
                assert numeric_fields(a) == numeric_fields(b), (kind, a, b)


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_both_languages_cite_the_same_numbers(scenario: str, seed: int) -> None:
    """S-5 on the rendered texts of every trigger."""
    for trigger in triggers(scenario, seed):
        built = pack(scenario, seed, trigger.id)
        versions = {(t.language, t.mode): t.text for t in render_texts(built)}
        for mode in ("analyst", "casual"):
            vi, en = versions[("vi", mode)], versions[("en", mode)]
            assert stat_numbers(vi) == stat_numbers(en), (trigger.id, vi, en)
        if trigger.kind in TEMPLATES:
            vi_lines = [n for line in render_lines(built, "vi") for n in stat_numbers(line.text)]
            en_lines = [n for line in render_lines(built, "en") for n in stat_numbers(line.text)]
            assert sorted(vi_lines) == sorted(en_lines), trigger.id


def test_the_choice_is_deterministic_and_never_uses_hash() -> None:
    assert stable_index("dominant_loser-42-m0008:analyst", 3) == stable_index(
        "dominant_loser-42-m0008:analyst", 3
    )
    assert [stable_index(f"k{i}", 3) for i in range(30)].count(0) not in (0, 30)
    assert pick(("{a}!", "{b}?"), "x", {"a": "1"}) == "1!"  # only usable phrasings
    assert pick(("{b}?",), "x", {"a": "1"}) is None
    built = pack("dominant_loser", 42, triggers("dominant_loser", 42)[5].id)
    assert render_version(built, "vi", "analyst") == render_version(built, "vi", "analyst")


def test_four_versions_in_a_fixed_order() -> None:
    built = pack("dominant_loser", 42, triggers("dominant_loser", 42)[0].id)
    assert [(t.language, t.mode) for t in render_texts(built)] == list(VERSIONS)


def test_graphic_words_exist_for_every_graphic_kind() -> None:
    for bank in (TITLES, SUBTITLES):
        for kind, (vi, en) in bank.items():
            assert vi and en and len(vi) == len(en), kind


def test_sentences_start_with_a_capital() -> None:
    assert capitalize_sentences("phút 36: VAR. phút 37! xG 0.31 ở phút 38.") == (
        "Phút 36: VAR. Phút 37! xG 0.31 ở phút 38."
    )
    assert (
        capitalize_sentences("xG cả trận: 1.25. Harbor lead.") == "xG cả trận: 1.25. Harbor lead."
    )
    assert capitalize_sentences("M. Ribas scores.") == "M. Ribas scores."
    for scenario, seed in SAMPLES:
        for trigger in triggers(scenario, seed):
            for text in render_texts(pack(scenario, seed, trigger.id)):
                assert not text.text[0].islower() or text.text.startswith("xG"), text.text
