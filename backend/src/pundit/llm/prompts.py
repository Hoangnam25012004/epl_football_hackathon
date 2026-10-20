"""Versioned prompts of the two LLM roles (docs/day2-design.md sections 4.4 and 6.2).

The analyst gets the whole match catalog (teams, players, metrics) in one call, so no tool loop
is needed. The prompt is compact (about 2k tokens) because a local model on a laptop CPU pays for
every token, and it is ordered for prefix caching: rules, examples and the match catalog form
the system message, identical for every claim of a match; only the clock and the claim change.
The explainer gets only verified facts. Traces record the prompt version and length, never the
text.
"""

import json
from typing import Any

from pundit.agents.contracts import ClaimContext, GuardrailViolation, JudgedClaim, MatchContext
from pundit.agents.guardrail import feedback_lines
from pundit.agents.wording import window_label

ANALYST_PROMPT_VERSION = "analyst-v2"
EXPLAINER_PROMPT_VERSION = "explainer-v4"
MAX_EXPLANATION_WORDS = 45

ANALYST_RULES = """\
You turn one football claim (Vietnamese or English) into a JSON spec for a deterministic \
verifier. Never compute or invent statistics: only describe what the claim asserts.

Rules
- Use only the team ids, player ids and metric names of the catalog below.
- kind "dominance": a team dominated / was on top / áp đảo / lấn lướt (teams only, no metric).
- kind "compare": two sides on one metric; op ">" more, "<" fewer, "==" equal. "twice as \
many" / "gấp đôi" -> ratio 2 with op ">". "Both teams equal" -> subject kind "both_teams" \
(id null), op "==", other null. A result ("won"/"thắng" -> ">", "lost"/"thua" -> "<", \
"drew"/"hoà" -> "==") is metric "score" against the opponent, window "full" unless a period \
is named.
- kind "value": one side and a stated number. op "==" exact, "~" approximate ("khoảng", \
"around", also approx=true), ">=" at least, "<=" at most, ">" / "<". "No corners" / "chưa có \
quả phạt góc nào" -> "==" 0. "Hardly any" / "gần như không" -> "<=" 2. A player who scored / \
ghi bàn -> metric "score" ">=" 1.
- ppda measures pressing and LOWER means stronger: "pressed harder" / "pressing mạnh hơn" \
-> op "<" on ppda.
- negated=true when the predicate is negated ("did not dominate", "không áp đảo"); "no \
corners" is value 0, not a negation.
- Windows: first_half, second_half ("hiệp 2", "45 phút cuối", "after the break"), \
last_minutes (minutes=N), from_minute, until_minute, minute_range, full (default).
- "A but B" / "A nhưng B": one assertion per clause, at most 3; a clause without a subject \
inherits the previous one. "Đội nhà" / "home side" is the home team, "đội khách" / "visitors" \
the away team.
- When unsure return assertions [] with unsupported_reason: opinions, predictions, referee \
bias -> unsupported_claim; a name not in the catalog -> unknown_entity; a name matching two \
players -> ambiguous_entity.
- text_span: the claim words of the assertion. note: at most 12 words.
Answer with the JSON object only. Fields not used by an assertion are null."""

# (claim, spec) pairs; null fields are left out to keep the prompt short.
FEW_SHOT: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "Đội nhà áp đảo hiệp 2",
        {"language": "vi", "assertions": [
            {"kind": "dominance", "subject": {"kind": "team", "id": "<home>"},
             "window": {"type": "second_half"}, "negated": False, "approx": False,
             "text_span": "Đội nhà áp đảo hiệp 2"}]},
    ),
    (
        "Harbor had twice as many shots as Kestrel in the second half",
        {"language": "en", "assertions": [
            {"kind": "compare", "subject": {"kind": "team", "id": "harbor_lions"},
             "other": {"kind": "team", "id": "kestrel_athletic"}, "metric": "shots", "op": ">",
             "ratio": 2.0, "window": {"type": "second_half"}, "negated": False, "approx": False,
             "text_span": "twice as many shots as Kestrel"}]},
    ),
    (
        "KES chưa có quả phạt góc nào trong 15 phút cuối",
        {"language": "vi", "assertions": [
            {"kind": "value", "subject": {"kind": "team", "id": "kestrel_athletic"},
             "metric": "corners", "op": "==", "value": 0,
             "window": {"type": "last_minutes", "minutes": 15}, "negated": False,
             "approx": False, "text_span": "chưa có quả phạt góc nào"}]},
    ),
    (
        "HAR không áp đảo hiệp 2 nhưng vẫn thua",
        {"language": "vi", "assertions": [
            {"kind": "dominance", "subject": {"kind": "team", "id": "harbor_lions"},
             "window": {"type": "second_half"}, "negated": True, "approx": False,
             "text_span": "HAR không áp đảo hiệp 2"},
            {"kind": "compare", "subject": {"kind": "team", "id": "harbor_lions"},
             "other": {"kind": "team", "id": "kestrel_athletic"}, "metric": "score", "op": "<",
             "window": {"type": "full"}, "negated": False, "approx": False,
             "text_span": "vẫn thua"}]},
    ),
    (
        "Trọng tài thiên vị Harbor",
        {"language": "vi", "assertions": [], "unsupported_reason": "unsupported_claim",
         "note": "referee bias is not measurable"},
    ),
)  # fmt: skip


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _who(scope: str, supports_player: bool) -> str:
    if scope == "player":
        return "player"
    return "team or player" if supports_player else "team"


def catalog_text(match: MatchContext) -> str:
    """Teams, players and metrics as short lines (the same for every claim of a match)."""
    home = match.teams[0].id
    lines = ["Teams (id | name | short name | side):"]
    lines += [f"- {t.id} | {t.name} | {t.short_name} | {t.side}" for t in match.teams]
    lines.append("Players (id | name | short name):")
    lines += [f"- {p.id} | {p.name} | {p.short_name}" for p in match.players]
    lines.append("Metrics (name | unit | for | meaning):")
    for m in match.metrics:
        if m.scope == "match":
            continue
        note = "; LOWER = stronger" if m.polarity < 0 else ""
        lines.append(
            f"- {m.name} | {m.unit} | {_who(m.scope, m.supports_player)} | {m.description_en}{note}"
        )
    examples = [
        f"Claim: {claim}\nJSON: {_compact(spec).replace('<home>', home)}"
        for claim, spec in FEW_SHOT
    ]
    return "\n".join(
        ["Examples (Harbor = harbor_lions, Kestrel = kestrel_athletic):", *examples, "", *lines]
    )


def analyst_messages(ctx: ClaimContext, feedback: list[str] | None = None) -> tuple[str, str]:
    """(system, user) for the ClaimAnalyst. The system part only depends on the match."""
    match = ctx.match
    system = f"{ANALYST_RULES}\n\n{catalog_text(match)}"
    state = "finished" if match.finished else "in play"
    user = [
        f"Clock: {match.clock_display} (period {match.period}, {state})",
        f"Claim language: {ctx.language}",
        f"Claim: {ctx.text}",
    ]
    if feedback:
        user.append("Your previous answer was rejected: " + "; ".join(feedback))
    return system, "\n".join(user)


EXPLAINER_SYSTEM = f"""\
You write the explanation shown under a fact-check verdict in a TV studio. One or two short \
sentences, at most {MAX_EXPLANATION_WORDS} words, in the requested language (vi = Vietnamese, \
en = English).
Rules
- Complete sentences that stand alone: start with the team, the player or the time window; never \
start or end with "..." and never start with và / nhưng / còn / and / but.
- Every sentence about a team, a player or the result cites at least one number from `facts`.
- Use only numbers from `facts`, written with digits exactly as in `display` (decimals with a dot).
- You may name the window, the clock and the score given in the input; no other number.
- Do not say the verdict: never write true / false / partly / insufficient, đúng / sai / \
đúng một phần / không đủ dữ liệu. The verdict is shown separately.
- Neutral tone, no speculation, no judgement of players, clubs or referees.
Answer with the JSON object {{"text": "..."}} only."""


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
    return EXPLAINER_SYSTEM, _compact(user)
