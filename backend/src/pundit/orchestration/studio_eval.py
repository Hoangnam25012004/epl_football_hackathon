"""Offline studio runs and their report (docs/day3-design.md sections 15.3 and 16.7).

`run_offline` runs the StudioService of a live session over every trigger of a finished match:
the same jobs, on views pinned at each trigger's seq, with the director's memory of rules, and
the recaps at half-time and full time. `StudioReport` measures what section 16.7 asks for:
counts and sources, the drafts the Guardrail rejected, leaks (published texts that fail a fresh
check: must be 0), an independent oracle for numbers, players and outcomes, the analyst/casual
differences D1-D4, the rules a beginner meets, the longest silence and the latency.
"""

import itertools
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from pundit.agents.collector import get_stat
from pundit.agents.contracts import (
    CommentaryItem,
    Fact,
    FactPack,
    Lang,
    MatchContext,
    Moment,
    Recap,
    RecapScope,
    Register,
    RuleId,
    StudioJob,
    StudioOutput,
    TalkingLine,
    TraceSpan,
    Trigger,
)
from pundit.agents.entities import find_entities
from pundit.agents.factpack import build_pack, plan
from pundit.agents.glossary import jargon_terms
from pundit.agents.graphics import wants_graphic
from pundit.agents.language import Text, detect_language, find_terms
from pundit.agents.moments import detect_all
from pundit.agents.numbers import extract_numbers
from pundit.agents.recap import RECAP_SCOPES, RecapPack, build_recap_pack, recap_scope
from pundit.agents.studio_guard import (
    StudioProduct,
    StudioScope,
    check_studio,
    scope_of,
    stat_number_count,
)
from pundit.agents.talking_points import wants_talking_point
from pundit.agents.triggers import detect_triggers
from pundit.domain.log import EventLog, LogView
from pundit.domain.percentiles import p95
from pundit.engine.match import GeneratedMatch
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.studio_service import StudioService

# items per match with the templates: section 16.7 estimated 60-110 on the four sample
# matches; the 4 x 3 matrix measured 53-99 (balanced-7 is a quiet match), see section 23.
# 28/10 (day7-design 7.6): stats every 5', small plays, lulls after 1.75' -> 110-154 measured
COMMENTARY_RANGE = (100, 180)
MAX_SILENCE_MIN = 3.0  # lulls come after 1.75', on the next event: 2.8' measured on the matrix
MIN_STRICT_D2 = 0.8
MAX_LLM_FAILURE_RATE = 0.2
MIN_LANGUAGE_WORDS = 3
BASE_RULES: frozenset[RuleId] = frozenset(
    {"offside", "corner", "yellow_card", "substitution", "added_time", "xg", "possession",
     "momentum"}
)  # fmt: skip
EXPECTED_RULES: dict[str, tuple[frozenset[RuleId], frozenset[RuleId]]] = {
    # match -> (rules a beginner must meet, rules that must not appear); table 9.4
    "dominant_loser-42": (BASE_RULES, frozenset({"var", "penalty", "red_card", "second_yellow"})),
    "comeback_var-7": (BASE_RULES | {"var"}, frozenset({"penalty", "red_card"})),
    "red_card_swing-7": (BASE_RULES | {"red_card"}, frozenset({"var", "penalty", "second_yellow"})),
    "balanced-3": (BASE_RULES, frozenset({"var", "penalty", "red_card"})),
}
NO_GOAL_WORDS = ("ghi bàn", "lập công", "vào lưới", "thành bàn", "scores", "scored", "netted")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_HALF = re.compile(r"(?i)\b(?:hiệp|h)\s?([12])\b")


# --------------------------------------------------------------------------- offline run


@dataclass
class OfflineRun:
    match: GeneratedMatch
    log: EventLog
    moments: list[Moment]
    triggers: list[Trigger]
    provider: str
    model: str
    outputs: list[StudioOutput] = field(default_factory=list)
    recaps: dict[RecapScope, list[Recap]] = field(default_factory=dict)  # revisions in order
    recap_packs: dict[RecapScope, RecapPack] = field(default_factory=dict)
    recap_traces: dict[RecapScope, tuple[TraceSpan, ...]] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)

    def view_at(self, seq: int) -> LogView:
        return self.log.view(seq + 1)


async def run_offline(
    match: GeneratedMatch,
    service: StudioService,
    recaps: RecapService | None = None,
    *,
    on_output: Callable[[Trigger, StudioOutput], None] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> OfflineRun:
    """Every trigger of the match through the studio, in order, like a live session."""
    meta, events = match.meta, match.events
    log = EventLog(meta)
    for event in events:
        log.append(event)
    run = OfflineRun(
        match=match,
        log=log,
        moments=detect_all(meta, events),
        triggers=detect_triggers(meta, events),
        provider=service.provider,
        model=service.model,
    )
    seen: set[RuleId] = set()
    for trigger in run.triggers:
        first = tuple(rule for rule in trigger.rules if rule not in seen)
        view = run.view_at(trigger.seq)
        started = clock()
        output = await service.run(StudioJob(trigger=trigger, first_rules=first), view)
        run.latencies_ms.append((clock() - started) * 1000)
        seen.update(output.commentary.rules)  # as the director does
        run.outputs.append(output)
        if on_output is not None:
            on_output(trigger, output)
        scope = RECAP_SCOPES.get(trigger.kind)
        if scope is not None and recaps is not None:
            moments = [m for m in run.moments if m.seq <= trigger.seq]
            pack = build_recap_pack(scope, view, moments)
            first_recap = recaps.template(pack)
            improved, trace = await recaps.improve(pack, first_recap)
            run.recaps[scope] = [first_recap] + ([improved] if improved is not None else [])
            run.recap_packs[scope] = pack
            run.recap_traces[scope] = trace.spans
    return run


def pack_of(trigger: Trigger, view: LogView) -> FactPack:
    """The FactPack of a trigger, built again (it is deterministic) for the checks."""
    calls = [get_stat(view, query) for query in plan(trigger, view.meta)]
    return build_pack(trigger, view, calls)


# --------------------------------------------------------------------------- oracle


def _as_written(token: str) -> str:
    """A number without the zeros that do not change it: "1.5" for an xG shown 1.50 is the
    number on screen (Day 6), "0.4" for 0.44 is not."""
    return token.rstrip("0").rstrip(".") if "." in token else token


def _allowed_numbers(
    facts: Iterable[Fact], match: MatchContext, minutes: Iterable[str], window: Iterable[float]
) -> set[str]:
    allowed: set[str] = set()
    for fact in facts:
        allowed |= {_as_written(token) for token in _NUMBER.findall(fact.display)}
    for minute in minutes:
        allowed |= set(_NUMBER.findall(minute))
    allowed |= {str(goals) for goals in match.score.values()}
    allowed |= {f"{number:g}" for number in window}
    return allowed


def oracle_problems(
    text: str,
    *,
    facts: Sequence[Fact],
    match: MatchContext,
    players: Iterable[str],
    minutes: Iterable[str],
    window_numbers: Iterable[float],
    no_goal: bool,
) -> list[str]:
    """Problems an independent reader finds (plain string matching, not the Guardrail's
    parser): a number no fact, minute, score or window shows; a player who is not part of the
    moment; a goal word for a shot that did not go in."""
    problems: list[str] = []
    allowed = _allowed_numbers(facts, match, minutes, window_numbers)
    for token in _NUMBER.findall(_HALF.sub(" ", text)):
        if _as_written(token) not in allowed:
            problems.append(f"number {token} matches no fact")
    allowed_players = set(players)
    for player in match.players:
        for name in {player.short_name, player.name}:
            if not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
                continue
            namesakes = {p.id for p in match.players if name in (p.short_name, p.name)}
            if not namesakes & allowed_players:
                problems.append(f"player {name} is not part of the moment")
    lowered = text.casefold()
    if no_goal and any(word in lowered for word in NO_GOAL_WORDS):
        problems.append("a goal word for a shot that did not go in")
    return problems


def _no_goal(trigger: Trigger) -> bool:
    return trigger.outcome in ("saved", "blocked", "off_target", "post")


def _pack_oracle(text: str, pack: FactPack, scope: StudioScope) -> list[str]:
    return oracle_problems(
        text,
        facts=pack.facts,
        match=pack.match,
        players=scope.players,
        minutes=[pack.trigger.display_minute],
        window_numbers=pack.window_numbers,
        no_goal=_no_goal(pack.trigger),
    )


# --------------------------------------------------------------------------- D1-D4


def numbers_in(text: str) -> int:
    return stat_number_count(extract_numbers(Text.of(text)))


def language_of(text: str, match: MatchContext) -> Lang | None:
    """The language of a text with names and jargon masked; None for very short texts."""
    written = Text.of(text)
    if sum(1 for word in text.split() if any(ch.isalpha() for ch in word)) < MIN_LANGUAGE_WORDS:
        return None
    masked = [(m.start, m.end) for m in find_entities(written, match)]
    masked += [(h.start, h.end) for h in find_terms(written, jargon_terms())]
    return detect_language(written, masked)


@dataclass
class Ratio:
    ok: int = 0
    total: int = 0

    def add(self, passed: bool) -> None:
        self.total += 1
        self.ok += int(passed)

    @property
    def rate(self) -> float:
        return self.ok / self.total if self.total else 1.0

    def __str__(self) -> str:
        return f"{100 * self.rate:.0f}% ({self.ok}/{self.total})"


# --------------------------------------------------------------------------- report


@dataclass
class StudioReport:
    match_id: str
    provider: str
    model: str
    counts: Counter[str] = field(default_factory=Counter)
    expected: Counter[str] = field(default_factory=Counter)
    sources: Counter[str] = field(default_factory=Counter)
    rejection_codes: Counter[str] = field(default_factory=Counter)
    drafts_rejected: int = 0
    template_rejections: int = 0
    leaks: list[str] = field(default_factory=list)
    oracle: list[str] = field(default_factory=list)
    d1: Ratio = field(default_factory=Ratio)
    d2: Ratio = field(default_factory=Ratio)
    d2_strict: Ratio = field(default_factory=Ratio)
    d3: Ratio = field(default_factory=Ratio)
    d4: Ratio = field(default_factory=Ratio)
    first_rules: list[tuple[str, RuleId]] = field(default_factory=list)
    rules: set[RuleId] = field(default_factory=set)
    silence_min: float = 0.0
    silence_at: str = ""
    latencies_ms: list[float] = field(default_factory=list)
    llm_calls: int = 0
    llm_failures: int = 0
    degraded: Counter[str] = field(default_factory=Counter)

    @property
    def mock(self) -> bool:
        return self.provider == "mock"

    def rules_problems(self) -> list[str]:
        expected = EXPECTED_RULES.get(self.match_id)
        if expected is None:
            return []
        must, must_not = expected
        problems = [f"rule {r} never explained" for r in sorted(must - self.rules)]
        problems += [f"rule {r} should not appear" for r in sorted(must_not & self.rules)]
        return problems

    def failures(self) -> list[str]:
        """Thresholds of section 16.7; an empty list means PASSED."""
        found: list[str] = []
        if self.leaks:
            found.append(f"{len(self.leaks)} published texts fail the Guardrail")
        if self.oracle:
            found.append(f"{len(self.oracle)} oracle problems")
        if self.template_rejections:
            found.append(f"{self.template_rejections} template texts rejected")
        for name in ("talking_point", "lower_third"):
            if self.counts[name] != self.expected[name]:
                found.append(
                    f"{self.counts[name]} {name}s, expected {self.expected[name]} (tables 6, 7.1)"
                )
        for name, ratio, minimum in (
            ("D1", self.d1, 1.0),
            ("D2", self.d2, 1.0),
            ("D2 strict", self.d2_strict, MIN_STRICT_D2),
            ("D3", self.d3, 1.0),
            ("D4", self.d4, 1.0),
        ):
            if ratio.rate < minimum:
                found.append(f"{name} {ratio} below {100 * minimum:.0f}%")
        found += self.rules_problems()
        if self.mock:
            low, high = COMMENTARY_RANGE
            if not low <= self.counts["commentary"] <= high:
                found.append(f"{self.counts['commentary']} commentary items, not {low}-{high}")
            if self.silence_min > MAX_SILENCE_MIN:
                found.append(f"silence of {self.silence_min:.1f} min ({self.silence_at})")
        elif self.llm_calls and self.llm_failures / self.llm_calls > MAX_LLM_FAILURE_RATE:
            found.append(
                f"LLM NOT WORKING: {self.llm_failures} of {self.llm_calls} calls failed "
                "(timeout or error)"
            )
        return found

    def lines(self) -> list[str]:
        def share(kind: str, first: str, second: str) -> str:
            counts = self.sources
            return f"{first} {counts[f'{kind}:{first}']} / {second} {counts[f'{kind}:{second}']}"

        latency_p50 = statistics.median(self.latencies_ms or [0.0])
        latency_p95 = p95(self.latencies_ms) or 0.0
        rules = ", ".join(f"{minute} {rule}" for minute, rule in self.first_rules) or "-"
        problems = self.rules_problems()
        codes = ", ".join(f"{c} {n}" for c, n in self.rejection_codes.most_common()) or "-"
        degraded = ", ".join(f"{d} {n}" for d, n in sorted(self.degraded.items())) or "-"
        return [
            f"studio report {self.match_id} | provider {self.provider} ({self.model}) | "
            "synthetic data",
            f"items       commentary {self.counts['commentary']} · talking points "
            f"{self.counts['talking_point']} (expected {self.expected['talking_point']}) · "
            f"lower-thirds {self.counts['lower_third']} (expected "
            f"{self.expected['lower_third']}) · recaps {self.counts['recap']} · speak "
            f"{self.counts['speak']}",
            f"sources     commentary {share('commentary', 'llm', 'template')} · talking points "
            f"{share('talking_point', 'llm', 'template')} · lower-thirds "
            f"{share('lower_third', 'llm', 'rule')} · recaps {share('recap', 'llm', 'template')}",
            f"guardrail   drafts rejected {self.drafts_rejected} ({codes}) · template "
            f"rejections {self.template_rejections} · leaks {len(self.leaks)} · oracle "
            f"{len(self.oracle)}",
            f"registers   D1 {self.d1} · D2 {self.d2} · D2 strict {self.d2_strict} · "
            f"D3 {self.d3} · D4 {self.d4}",
            f"beginner    {rules}",
            f"            expected rules: {'; '.join(problems) if problems else 'ok'}"
            if self.match_id in EXPECTED_RULES
            else "            expected rules: n/a (table 9.4 covers the four sample matches)",
            f"continuity  longest silence {self.silence_min:.1f} min ({self.silence_at})",
            f"latency     job p50 {latency_p50:.0f} ms · p95 {latency_p95:.0f} ms · "
            f"llm calls {self.llm_calls} · failures {self.llm_failures}",
            f"degraded    {degraded}",
            *(f"LEAK        {leak}" for leak in self.leaks[:10]),
            *(f"ORACLE      {problem}" for problem in self.oracle[:10]),
        ]

    @property
    def passed(self) -> bool:
        return not self.failures()


def _silence(items: Sequence[CommentaryItem]) -> tuple[float, str]:
    """Longest gap between consecutive commentary items of one half (match minutes)."""
    longest, where = 0.0, "-"
    for previous, current in itertools.pairwise(items):
        if previous.period != current.period:
            continue
        gap = (current.clock_ms - previous.clock_ms) / 60_000
        if gap > longest:
            longest = gap
            where = f"{previous.display_minute}-{current.display_minute}"
    return longest, where


def _trace_stats(report: StudioReport, spans: Iterable[TraceSpan]) -> None:
    for span in spans:
        if span.kind == "llm":
            report.llm_calls += 1
            report.llm_failures += int(span.status == "error")
        if span.kind == "guardrail" and span.status == "rejected":
            agent = str(span.input.get("agent", ""))
            if agent.startswith("Template"):
                report.template_rejections += 1
            else:
                report.drafts_rejected += 1
                for violation in span.output.get("violations", []):
                    if isinstance(violation, dict):
                        report.rejection_codes[str(violation.get("code"))] += 1


def _check(
    report: StudioReport,
    where: str,
    text: str,
    scope: StudioScope,
    *,
    language: Lang,
    product: StudioProduct,
    model_text: bool,
    mode: Register | None = None,
    part: str | None = None,
) -> None:
    result = check_studio(
        text,
        scope,
        language=language,
        product=product,
        mode=mode,
        part=part,
        model_text=model_text,
    )
    if not result.passed:
        codes = ", ".join(v.code for v in result.violations)
        report.leaks.append(f"{where}: {codes}: {text}")


def build_report(run: OfflineRun) -> StudioReport:
    meta = run.match.meta
    report = StudioReport(match_id=meta.match_id, provider=run.provider, model=run.model)
    report.latencies_ms = list(run.latencies_ms)
    items: list[CommentaryItem] = []
    for trigger, output in zip(run.triggers, run.outputs, strict=True):
        pack = pack_of(trigger, run.view_at(trigger.seq))
        scope = scope_of(pack)
        item = output.commentary
        items.append(item)
        report.counts["commentary"] += 1
        report.counts["speak"] += int(item.speak)
        report.expected["talking_point"] += int(wants_talking_point(trigger))
        report.expected["lower_third"] += int(wants_graphic(trigger))
        report.degraded.update(output.degraded)
        report.rules.update(item.rules)
        report.first_rules += [(item.display_minute, rule) for rule in item.first_rules]
        _trace_stats(report, output.trace or ())
        for text in item.texts:
            where = f"{item.id} {text.language}/{text.mode} ({text.source})"
            report.sources[f"commentary:{text.source}"] += 1
            _check(
                report,
                where,
                text.text,
                scope,
                language=text.language,
                product="commentary",
                mode=text.mode,
                model_text=text.source == "llm",
            )
            report.oracle += [
                f"{where}: {p}: {text.text}" for p in _pack_oracle(text.text, pack, scope)
            ]
            found = language_of(text.text, pack.match)
            report.d4.add(found is None or found == text.language)
            if text.mode == "casual":
                report.d3.add(not find_terms(Text.of(text.text), jargon_terms()))
        languages: tuple[Lang, ...] = ("vi", "en")
        for language in languages:
            analyst, casual = (
                item.text(language, "analyst").text,
                item.text(language, "casual").text,
            )
            if item.facts:
                report.d1.add(analyst != casual)
            report.d2.add(numbers_in(analyst) >= numbers_in(casual))
            if len(item.facts) >= 2:
                report.d2_strict.add(numbers_in(analyst) > numbers_in(casual))
        if output.talking_point is not None:
            tp = output.talking_point
            report.counts["talking_point"] += 1
            sides: tuple[tuple[Lang, tuple[TalkingLine, ...], str], ...] = (
                ("en", tp.en, tp.source_en),
                ("vi", tp.vi, tp.source_vi),
            )
            for language, lines, source in sides:
                report.sources[f"talking_point:{source}"] += 1
                for line in lines:
                    where = f"{tp.id} {language} ({source})"
                    _check(
                        report,
                        where,
                        line.text,
                        scope,
                        language=language,
                        product="talking_point",
                        model_text=source == "llm",
                    )
                    report.oracle += [
                        f"{where}: {p}: {line.text}" for p in _pack_oracle(line.text, pack, scope)
                    ]
        if output.lower_third is not None:
            lt = output.lower_third
            report.counts["lower_third"] += 1
            report.sources[f"lower_third:{lt.source}"] += 1
            words: tuple[tuple[Lang, str, str], ...] = (
                ("vi", lt.title_vi, lt.subtitle_vi),
                ("en", lt.title_en, lt.subtitle_en),
            )
            for language, title, subtitle in words:
                for part, shown in (("title", title), ("subtitle", subtitle)):
                    where = f"{lt.id} {language} {part}"
                    _check(
                        report,
                        where,
                        shown,
                        scope,
                        language=language,
                        product="graphic",
                        part=part,
                        model_text=False,
                    )
                    report.oracle += [
                        f"{where}: {p}: {shown}" for p in _pack_oracle(shown, pack, scope)
                    ]
    for scope_name, revisions in run.recaps.items():
        recap_pack = run.recap_packs[scope_name]
        rscope = recap_scope(recap_pack)
        _trace_stats(report, run.recap_traces.get(scope_name, ()))
        report.counts["recap"] += 1
        for recap in revisions:
            for version in recap.texts:
                if recap is revisions[-1]:
                    report.sources[f"recap:{version.source}"] += 1
                for index, paragraph in enumerate((version.title, *version.paragraphs)):
                    where = (
                        f"{recap.id} r{recap.revision} {version.language}/{version.mode} #{index}"
                    )
                    _check(
                        report,
                        where,
                        paragraph,
                        rscope,
                        language=version.language,
                        product="recap",
                        mode=version.mode,
                        part="paragraph",
                        model_text=version.source == "llm",
                    )
                    problems = oracle_problems(
                        paragraph,
                        facts=recap_pack.facts,
                        match=recap_pack.match,
                        players=recap_pack.players,
                        minutes=[],
                        window_numbers=recap_pack.window_numbers,
                        no_goal=False,
                    )
                    report.oracle += [f"{where}: {p}: {paragraph}" for p in problems]
    report.silence_min, report.silence_at = _silence(items)
    return report
