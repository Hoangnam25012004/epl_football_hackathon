"""Versioned prompts of the two LLM roles (docs/day2-design.md sections 4.4 and 6.2).

The analyst gets the whole match catalog in one message (teams, players, metrics, clock), so
one call is enough and no tool loop is needed. The explainer gets only verified facts. Traces
record the prompt version and length, never the text.
"""

import json
from typing import Any

from pundit.agents.contracts import ClaimContext, GuardrailViolation, JudgedClaim
from pundit.agents.guardrail import feedback_lines
from pundit.agents.wording import window_label

ANALYST_PROMPT_VERSION = "analyst-v1"
EXPLAINER_PROMPT_VERSION = "explainer-v1"
MAX_EXPLANATION_WORDS = 45

ANALYST_SYSTEM = """\
You turn one football claim (Vietnamese or English) into a JSON spec for a deterministic \
verifier. You never compute, look up or invent statistics: you only describe what the claim \
asserts. A separate program computes the numbers and the verdict.

Rules
- Use only the team ids, player ids and metric names given in `match`.
- kind "dominance": a team dominated / was on top / áp đảo / lấn lướt (teams only, no metric).
- kind "compare": two sides on one metric. op ">" more, "<" fewer, "==" equal. \
"twice as many" / "gấp đôi" -> ratio 2 with op ">". "both teams equal" -> subject \
{"kind":"both_teams","id":null}, op "==", other null. A result ("won", "thắng" -> op ">", \
"lost", "thua" -> op "<", "drew", "hoà" -> op "==") is metric "score" with window "full" \
unless the claim names a period.
- kind "value": one side and a stated number. op "==" exact, "~" approximate \
("khoảng", "around"; also set approx=true), ">=" at least, "<=" at most, ">" / "<". \
"no corners" / "chưa có quả phạt góc nào" -> op "==" value 0. "hardly any" / "gần như không" \
-> op "<=" value 2. A player who "scored" / "ghi bàn" -> metric "score", op ">=", value 1.
- Polarity: ppda is pressing; LOWER ppda = stronger pressing. "pressed more / harder", \
"pressing mạnh hơn" -> op "<" on ppda.
- negated=true when the predicate is negated ("did not dominate", "không áp đảo"); \
"no corners" is value 0, not a negation.
- Windows: "first_half", "second_half" ("hiệp 2", "45 phút cuối", "after the break"), \
"last_minutes" (minutes=N), "from_minute", "until_minute", "minute_range", "full" (default).
- "A but B", "A nhưng B": one assertion per clause, at most 3. A clause without its own \
subject inherits the previous subject.
- When unsure, return "assertions": [] with unsupported_reason: opinions, predictions, \
referee bias -> "unsupported_claim"; a name that is not in the match -> "unknown_entity"; \
a name matching two players -> "ambiguous_entity".
- text_span: the words of the claim that produced the assertion. note: at most 12 words.
Return only the JSON object for the schema."""

_W_FULL = {"type": "full", "minutes": None, "start_minute": None, "end_minute": None}
_W_H2 = {"type": "second_half", "minutes": None, "start_minute": None, "end_minute": None}


def _team(team_id: str) -> dict[str, Any]:
    return {"kind": "team", "id": team_id}


def _a(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "value", "subject": None, "other": None, "metric": None, "op": None,
        "value": None, "ratio": None, "window": _W_FULL, "negated": False, "approx": False,
        "text_span": "",
    }  # fmt: skip
    base.update(fields)
    return base


FEW_SHOT: list[dict[str, Any]] = [
    {
        "claim": "Đội nhà áp đảo hiệp 2",
        "spec": {"language": "vi", "unsupported_reason": None, "note": "home side = harbor_lions",
                 "assertions": [_a(kind="dominance", subject=_team("harbor_lions"), window=_W_H2,
                                   text_span="Đội nhà áp đảo hiệp 2")]},
    },
    {
        "claim": "Harbor had twice as many shots as Kestrel in the second half",
        "spec": {"language": "en", "unsupported_reason": None, "note": "ratio 2",
                 "assertions": [_a(kind="compare", subject=_team("harbor_lions"),
                                   other=_team("kestrel_athletic"), metric="shots", op=">",
                                   ratio=2.0, window=_W_H2,
                                   text_span="twice as many shots as Kestrel")]},
    },
    {
        "claim": "KES chưa có quả phạt góc nào trong hiệp 2",
        "spec": {"language": "vi", "unsupported_reason": None, "note": "zero corners",
                 "assertions": [_a(subject=_team("kestrel_athletic"), metric="corners", op="==",
                                   value=0.0, window=_W_H2,
                                   text_span="chưa có quả phạt góc nào")]},
    },
    {
        "claim": "HAR không áp đảo hiệp 2 nhưng vẫn thua",
        "spec": {"language": "vi", "unsupported_reason": None, "note": "negation + result",
                 "assertions": [
                     _a(kind="dominance", subject=_team("harbor_lions"), window=_W_H2,
                        negated=True, text_span="HAR không áp đảo hiệp 2"),
                     _a(kind="compare", subject=_team("harbor_lions"),
                        other=_team("kestrel_athletic"), metric="score", op="<",
                        text_span="vẫn thua")]},
    },
    {
        "claim": "Harbor pressed harder than Kestrel",
        "spec": {"language": "en", "unsupported_reason": None, "note": "lower ppda = harder press",
                 "assertions": [_a(kind="compare", subject=_team("harbor_lions"),
                                   other=_team("kestrel_athletic"), metric="ppda", op="<",
                                   text_span="pressed harder than Kestrel")]},
    },
    {
        "claim": "Trọng tài thiên vị Harbor",
        "spec": {"language": "vi", "unsupported_reason": "unsupported_claim",
                 "note": "referee bias is not measurable", "assertions": []},
    },
]  # fmt: skip


def match_catalog(ctx: ClaimContext) -> dict[str, Any]:
    match = ctx.match
    return {
        "match_id": match.match_id,
        "clock": match.clock_display,
        "period": match.period,
        "finished": match.finished,
        "teams": [
            {"id": t.id, "name": t.name, "short_name": t.short_name, "side": t.side}
            for t in match.teams
        ],
        "players": [
            {"id": p.id, "team": p.team, "name": p.name, "short_name": p.short_name}
            for p in match.players
        ],
        "metrics": [
            {
                "name": m.name,
                "unit": m.unit,
                "scope": m.scope,
                "player": m.supports_player,
                "polarity": m.polarity,
                "about": m.description_en,
            }
            for m in match.metrics
            if m.scope != "match"
        ],
    }


def analyst_messages(ctx: ClaimContext, feedback: list[str] | None = None) -> tuple[str, str]:
    """(system, user) for the ClaimAnalyst."""
    user: dict[str, Any] = {
        "examples": FEW_SHOT,
        "match": match_catalog(ctx),
        "claim": ctx.text,
        "claim_language": ctx.language,
    }
    if feedback:
        user["your_previous_answer_was_rejected"] = feedback
    return ANALYST_SYSTEM, json.dumps(user, ensure_ascii=False)


EXPLAINER_SYSTEM = f"""\
You write the explanation shown under a fact-check verdict in a TV studio. One or two short \
sentences, at most {MAX_EXPLANATION_WORDS} words, in the requested language (vi = Vietnamese, \
en = English).
Rules
- Use only numbers from `facts`, written with digits exactly as in `display` (decimals with a dot).
- You may name the window, the clock and the score given in the input; no other number.
- Do not say the verdict: never write true / false / partly / insufficient, đúng / sai / \
đúng một phần / không đủ dữ liệu. The verdict is shown separately.
- Neutral tone, no speculation, no judgement of players, clubs or referees.
Return only the JSON object {{"text": "..."}}."""


def explainer_messages(
    judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
) -> tuple[str, str]:
    """(system, user) for the Explainer."""
    ctx = judged.context
    facts = [
        {"label": f.label_vi if judged.language == "vi" else f.label_en, "display": f.display}
        for f in judged.all_facts()
    ]
    windows = sorted({window_label(r.assertion.window, judged.language) for r in judged.results})
    user: dict[str, Any] = {
        "claim": judged.text,
        "language": judged.language,
        "verdict_for_context_only": judged.verdict,
        "windows": windows,
        "clock": ctx.clock_display,
        "score": {t.short_name: ctx.score.get(t.id, 0) for t in ctx.teams},
        "facts": facts,
    }
    if feedback:
        user["your_previous_text_was_rejected"] = feedback_lines(feedback)
    return EXPLAINER_SYSTEM, json.dumps(user, ensure_ascii=False)
