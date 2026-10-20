"""Guardrail relation checks, metamorphic (design section 7, update 19.10).

For every count metric, window and order of the two teams, true sentences written in several
shapes (Vietnamese and English, with "hơn / than", "so với", "against"...) must pass; the same
sentences with the two numbers swapped, or with the comparison turned around, must be caught.
"""

from dataclasses import dataclass
from functools import cache

import pytest
from helpers import claim_context, generated, view_at

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, JudgedClaim
from pundit.agents.guardrail import check_judged
from pundit.agents.judge import judge

MATCHES = [("dominant_loser", 42), ("comeback_var", 7)]
WINDOWS = ("trong hiệp 1", "trong hiệp 2", "cả trận")
COUNTS = {  # metric -> (Vietnamese noun, English noun)
    "shots": ("cú sút", "shots"),
    "corners": ("quả phạt góc", "corners"),
    "fouls": ("lần phạm lỗi", "fouls"),
    "passes": ("đường chuyền", "passes"),
    "box_entries": ("lần vào vòng cấm", "box entries"),
}


@dataclass(frozen=True)
class Pair:
    claim: JudgedClaim
    a: str  # team names as a model would write them
    b: str
    va: str  # their values, as written
    vb: str
    vi: str  # the metric's nouns
    en: str

    @property
    def a_more(self) -> bool:
        return int(self.va) > int(self.vb)

    def right(self) -> list[str]:
        a, b, va, vb, vi, en = self.a, self.b, self.va, self.vb, self.vi, self.en
        out = [
            f"{a} có {va} {vi}, {b} có {vb}.",
            f"{a} có {va} {vi} so với {vb} của {b}.",
            f"{a} had {va} {en} and {b} had {vb}.",
            f"{b} had {vb} {en}, against {va} for {a}.",
        ]
        if self.a_more:
            out += [
                f"{a} có nhiều {vi} hơn {b}: {va} so với {vb}.",
                f"{a} có nhiều {vi} hơn {b} với {va} so với {vb}.",
                f"{b} chỉ có {vb} {vi}, ít hơn nhiều so với {va} của {a}.",
                f"{a} had more {en} than {b}, {va} to {vb}.",
                f"{a} had more {en} than {b} with {va} to {vb}.",
                f"{b} had fewer {en} than {a}: {vb} against {va}.",
                f"{a} có nhiều {vi} hơn {b}, {va}-{vb}.",
                f"{b} có ít {vi} hơn {a}, {vb}-{va}.",
                f"{a} có nhiều {vi} hơn {b} với {va} {vi}.",
                f"{b} had fewer {en} than {a}, with {vb} {en}.",
            ]
        return out

    def wrong(self) -> list[str]:
        a, b, va, vb, vi, en = self.a, self.b, self.va, self.vb, self.vi, self.en
        out = [f"{a} có {vb} {vi}, {b} có {va}.", f"{a} had {vb} {en} and {b} had {va}."]
        if self.a_more:
            out += [
                f"{b} có nhiều {vi} hơn {a}: {vb} so với {va}.",
                f"{a} had fewer {en} than {b}, {va} to {vb}.",
                f"{b} có nhiều {vi} hơn {a}, {va}-{vb}.",  # eval e03: the pair turned around
                f"{b} có nhiều {vi} hơn {a} với {va} {vi}.",  # one number, sides wrong
                f"{a} had fewer {en} than {b}, with {va} {en}.",
            ]
        return out


@cache
def judged(text: str, scenario: str, seed: int) -> JudgedClaim:
    ctx = claim_context(text, scenario=scenario, seed=seed)
    analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
    return judge(collect(analysis, view_at(scenario, seed)))


def pairs(scenario: str, seed: int) -> list[Pair]:
    meta = generated(scenario, seed).meta
    out = []
    for window in WINDOWS:
        for first, second in (meta.team_ids, meta.team_ids[::-1]):
            one, two = meta.team_info(first), meta.team_info(second)
            for metric, (vi, en) in COUNTS.items():
                claim = judged(
                    f"{one.short_name} có nhiều {vi} hơn {two.short_name} {window}", scenario, seed
                )
                by_team = {
                    f.team: f.value
                    for f in claim.all_facts()
                    if f.metric == metric and not f.derived and f.player is None
                }
                assert set(by_team) == {first, second}, claim.text
                va, vb = f"{by_team[first]:.0f}", f"{by_team[second]:.0f}"
                out.append(Pair(claim, one.name, two.name, va, vb, vi, en))
    return out


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_true_sentences_pass(scenario: str, seed: int) -> None:
    texts = [(text, pair.claim) for pair in pairs(scenario, seed) for text in pair.right()]
    rejected = [
        (text, [v.code for v in check_judged(text, claim).violations])
        for text, claim in texts
        if not check_judged(text, claim).passed
    ]
    assert len(texts) > 150
    assert rejected == [], rejected[:5]


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_swapped_numbers_and_turned_comparisons_are_caught(scenario: str, seed: int) -> None:
    checked, missed = 0, []
    for pair in pairs(scenario, seed):
        derived = {f"{f.value:.0f}" for f in pair.claim.all_facts() if f.derived}
        if pair.va == pair.vb or {pair.va, pair.vb} & derived:
            continue  # a coincidence makes the swapped sentence true or ambiguous
        for text in pair.wrong():
            checked += 1
            found = {v.code for v in check_judged(text, pair.claim).violations}
            if not found & {"WRONG_ATTRIBUTION", "WRONG_COMPARISON"}:
                missed.append(text)
    assert checked > 50
    assert missed == [], missed[:5]
