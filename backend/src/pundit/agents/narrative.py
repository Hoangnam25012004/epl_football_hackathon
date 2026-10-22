"""Narrative rules of the studio Guardrail (P6 review of the real-model runs, docs/day3-design.md
section 23.5).

Every number of a line can be right and the story still wrong. These checks read the words
around team names against what the FactPack knows at the trigger:

- score state: "X lead / are ahead / dẫn trước" needs X in front, nobody leads a level match,
  and "equalise / gỡ hòa / tying the score" needs a level score; a lead that was lost or is
  still sought ("cancels out Kestrel's lead", "pushing for an equaliser") claims nothing;
- caused goals: "this pressure led to the goal" where nobody scored (a "goal" outcome word);
- direction: at a momentum shift or a dangerous spell the game tilts "in favour of" the trigger
  team, and that team is not the one "under pressure" / "chịu áp lực";
- for text a model wrote: no run of English words inside a Vietnamese line ("đi off target",
  "tại full time") and no misspelled team name ("Haror Lions FC").

A recap tells the whole match ("Harbor took the lead before Kestrel came back"), so the score
state is only read at a live trigger.
"""

import re
from collections.abc import Sequence
from typing import Literal

from pundit.agents.contracts import GuardrailViolation, MatchContext, Trigger
from pundit.agents.entities import Mention
from pundit.agents.guardrail import _sentences
from pundit.agents.language import Text

ScoreClaim = Literal["lead", "trail", "level"]
DIRECTION_KINDS = frozenset({"momentum_shift", "dangerous_spell"})

_SCORE = r"\d+\s*[-\u2013]\s*\d+"  # hyphen or en dash
_NOT_SCORE_LEAD = r"(?!\s+(?:to|into)\b)(?!-)"  # "their attacks lead to chances", "lead-up"
_LEAD_EN = re.compile(
    rf"\b(?:the|a|an|their|its|his|her)\s+(?:[\w-]+\s+)?lead\b{_NOT_SCORE_LEAD}"
    rf"|'s\s+(?:[\w-]+\s+)?lead\b{_NOT_SCORE_LEAD}"
    r"|\bleads\b(?!\s+(?:to|into|the line|the attack|the press|the way|the charge)\b)"
    r"|\bleading\b(?!\s+(?:to|into|the line|the attack|the press|the way|the charge|scorer|"
    r"goalscorer)\b)"
    rf"|\blead(?=\s+{_SCORE})"
    r"|\bahead\b(?!\s+of\b)"
    r"|\bin front\b(?!\s+of\b)"
)
_TRAIL_EN = re.compile(r"\btrail(?:s|ing)?\b")
_LEVEL_EN = re.compile(
    r"\b(?:equali[sz](?:e|es|ed|ing|er)"
    r"|level(?:s|led|ed|ling|ing)?\s+(?:the\s+)?(?:score|scores|match|game|things)"
    r"|(?:draw|draws|drew|pull|pulls|pulled|pulling)\s+level"
    r"|(?:tie|ties|tied|tying)\s+(?:the\s+)?(?:score|scores|game|match)"
    r"|all\s+square|on\s+level\s+terms|level\s+at)\b"
)
_LEAD_VI = re.compile(
    r"(?<!\w)(?:dẫn trước|vươn lên dẫn|vượt lên dẫn|dẫn điểm)(?!\w)"
    rf"|(?<!\w)dẫn(?=\s+{_SCORE})"
)
_LEVEL_VI = re.compile(
    r"(?<!\w)(?:gỡ hòa|gỡ hoà|cân bằng tỷ số|san bằng tỷ số|san bằng cách biệt|"
    r"cân bằng cách biệt|tỷ số hòa|tỷ số hoà)(?!\w)"
    rf"|(?<!\w)(?:hòa|hoà)(?=\s+{_SCORE})"
)
# words right before a claim that take it back: a lead lost, sought or nearly taken
_UNSAID_BEFORE = frozenset(
    {
        "lost", "lose", "loses", "losing", "cancel", "cancels", "cancelled", "canceled",
        "cancelling", "wipe", "wipes", "wiped", "wiping", "erase", "erases", "erased", "erasing",
        "surrender", "surrenders", "surrendered", "throw", "throws", "threw", "thrown", "blew",
        "blown", "no", "not", "never", "without", "cut", "cuts", "reduce", "reduces", "reduced",
        "halve", "halves", "halved", "end", "ends", "ended", "for", "chase", "chases",
        "chasing", "seek", "seeks", "seeking", "search", "searching", "hunt", "hunting", "want",
        "wants", "need", "needs", "if", "could", "would", "might", "nearly", "almost", "denied",
        "mất", "không", "chưa", "xóa", "xoá", "tìm", "cần", "muốn", "hướng", "nhằm", "suýt",
        "nếu", "thể", "rút",
    }
)  # fmt: skip
_UNSAID_AFTER = frozenset(
    {"gone", "vanished", "disappeared", "evaporated", "cancelled", "canceled", "wiped", "erased"}
)
_LOOK_WORDS = 4
_WORD = re.compile(r"[^\W\d_]+")
_FOR_SIDE = re.compile(r"\s+(?:for|cho)\s+(?:the\s+)?")  # "a 1-0 lead for Kestrel"
_POSSESSIVE = re.compile(r"(?:their|its|his|her)\s")
_CLAUSE_BREAK = re.compile(
    r"[,;:]|\b(?:and|but|while|whereas)\b|(?<!\w)(?:nhưng|trong khi|còn)(?!\w)"
)

# direction: the text right before a team name, or right after it
_FAVOURS = re.compile(
    r"(?:in favou?r of|(?:tilts?|tilting|tilted|swings?|swinging|swung|shifts?|shifting|shifted)"
    r"\s+(?:towards?|to)|nghiêng(?: hẳn| hoàn toàn)? về(?: phía)?|đổi chiều về(?: phía)?|"
    r"thuộc về)\s+(?:the\s+)?$"
)
_PRESSED_BEFORE = re.compile(r"(?:pressure on|áp lực lên|sức ép lên|dồn ép|ép sân)\s+(?:the\s+)?$")
_PRESSED_AFTER = re.compile(
    r"^\s+(?:(?:is|are|was|were|has been|have been|had been|remain|remains|stay|stays|still|now|"
    r"really|đang|vẫn|đã|liên tục|tiếp tục)\s+){0,3}"
    r"(?:under\s+(?:(?:a lot of|so much|heavy|intense|sustained|constant|real|huge|more|"
    r"mounting|severe|serious|relentless|great|considerable)\s+)?pressure|on the back foot|"
    r"pinned back|chịu(?:\s+(?:nhiều|liên tục|sự))?\s+(?:áp lực|sức ép)|bị(?:\s+dồn)?\s+ép|"
    r"bị áp đảo|lép vế)(?!\w)"
)
_LOOK_CHARS = 40

# English words inside a Vietnamese line: a token that cannot be a Vietnamese syllable
_VI_SYLLABLE = re.compile(
    r"(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnprstvx])?"
    r"(?:oai|oao|oay|oeo|uya|uyu|uye|uay|uai|uoi|uou|ieu|yeu|ai|ao|au|ay|eo|eu|ia|ie|iu|oa|oe|"
    r"oi|oo|ua|ue|ui|uo|uu|uy|ye|[aeiouy])"
    r"(?:ch|ng|nh|[cmnpt])?"
)
# English words shaped like a Vietnamese syllable, and units: they neither count nor break a run
_NEUTRAL_ASCII = frozenset(
    {"a", "an", "at", "by", "the", "in", "on", "to", "so", "no", "go", "he", "we", "me", "be",
     "do", "it", "up", "km", "kmh", "mph"}
)  # fmt: skip
# capitalised words are names unless they are common English words
_CAPITAL_EN = frozenset(
    {"off", "full", "half", "time", "target", "goal", "shot", "spell", "pressure", "big",
     "chance", "match", "game", "first", "second", "minute", "lead", "save", "kick", "free",
     "corner", "with", "after", "before", "this", "that", "what", "their", "his", "they",
     "of", "and", "but", "for", "from", "had", "has", "have", "was", "were", "is", "are"}
)  # fmt: skip
MIN_ENGLISH_RUN = 2
MIN_NAME_WORD = 5


def _sentence_of(text: Text, index: int) -> tuple[int, int]:
    for start, end in _sentences(text):
        if start <= index <= end:
            return start, end
    return 0, len(text.raw)


def _words_before(text: Text, start: int, floor: int) -> list[str]:
    return _WORD.findall(text.lower[floor:start])[-_LOOK_WORDS:]


def _unsaid(text: Text, start: int, end: int, sentence: tuple[int, int]) -> bool:
    before = _words_before(text, start, sentence[0])
    after = _WORD.findall(text.lower[end : sentence[1]])[:3]
    return any(w in _UNSAID_BEFORE for w in before) or any(w in _UNSAID_AFTER for w in after)


def _team_of(mention: Mention) -> str | None:
    if mention.ambiguous or mention.kind == "both_teams":
        return None
    return mention.team


def _subject(
    text: Text, start: int, end: int, sentence: tuple[int, int], mentions: Sequence[Mention]
) -> str | None:
    """The side a claim is about: "a 1-0 lead for X", else the last name before it; "their
    lead" in a clause without a name of its own ("…, maintaining their lead") is the lead of
    the subject of the sentence."""
    side = _FOR_SIDE.match(text.lower, end)
    if side is not None:
        after = [m for m in mentions if m.start == side.end()]
        if after:
            return _team_of(after[0])
    before = [m for m in mentions if sentence[0] <= m.start and m.end <= start]
    if not before:
        return None
    if _POSSESSIVE.match(text.lower, start):
        breaks = list(_CLAUSE_BREAK.finditer(text.lower, sentence[0], start))
        clause_start = breaks[-1].end() if breaks else sentence[0]
        if not any(m.start >= clause_start for m in before):
            return _team_of(before[0])
    return _team_of(before[-1])


_EN_CLAIMS: tuple[tuple[re.Pattern[str], ScoreClaim], ...] = (
    (_LEAD_EN, "lead"),
    (_TRAIL_EN, "trail"),
    (_LEVEL_EN, "level"),
)


def _claims(text: Text) -> list[tuple[int, int, ScoreClaim]]:
    lower = text.lower
    found: list[tuple[int, int, ScoreClaim]] = []
    for pattern, claim in _EN_CLAIMS:
        found += [(m.start(), m.end(), claim) for m in pattern.finditer(lower)]
    for m in _LEAD_VI.finditer(lower):
        trail = _WORD.findall(lower[: m.start()])[-1:] == ["bị"]  # "bị dẫn trước"
        found.append((m.start(), m.end(), "trail" if trail else "lead"))
    found += [(m.start(), m.end(), "level") for m in _LEVEL_VI.finditer(lower)]
    return sorted(found)


def score_state_violations(
    text: Text, mentions: Sequence[Mention], match: MatchContext, trigger: Trigger | None
) -> list[GuardrailViolation]:
    """Lead, trail and level words against the score at the trigger."""
    if trigger is None:
        return []
    home, away = match.teams[0], match.teams[1]
    goals = {home.id: match.score.get(home.id, 0), away.id: match.score.get(away.id, 0)}
    shown = f"{home.name} {goals[home.id]}-{goals[away.id]} {away.name}"
    level = goals[home.id] == goals[away.id]
    found: list[GuardrailViolation] = []
    for start, end, claim in _claims(text):
        sentence = _sentence_of(text, start)
        if _unsaid(text, start, end, sentence):
            continue
        phrase = text.raw[start:end]
        problem: str | None = None
        if claim == "level":
            if not level:
                problem = f"the score is {shown}, not level"
        elif level:
            problem = f"the score is level ({shown}): nobody leads"
        else:
            side = _subject(text, start, end, sentence, mentions)
            if side is not None:
                other = away.id if side == home.id else home.id
                ahead = goals[side] > goals[other]
                if ahead != (claim == "lead"):
                    where = "behind" if claim == "lead" else "in front"
                    problem = f"{match.team(side).name} are {where} ({shown})"
        if problem is not None:
            found.append(
                GuardrailViolation(
                    code="WRONG_OUTCOME", detail=f"{phrase!r} does not fit: {problem}", span=phrase
                )
            )
    return found


_CAUSED_GOAL = re.compile(
    r"\b(?:led|leading|lead|leads|resulted|resulting|result|results|paid off|pays off|produced|"
    r"producing|yielded)\s+(?:(?:to|in|with)\s+)?(?:[\w'-]+\s+){0,3}?"
    r"(?:goal|opener|equali[sz]er|winner)\b"
    r"(?![\s-]+(?:kicks?|lines?|mouth|area|posts?|frame|keeper|bound|scoring)\b)"
)


def caused_goal_spans(text: Text) -> list[tuple[int, int]]:
    """ "This sustained pressure has led to the goal": a goal claimed without "scored"."""
    return [
        (m.start(), m.end())
        for m in _CAUSED_GOAL.finditer(text.lower)
        if not m.group(0).endswith("on goal")
    ]


def direction_violations(
    text: Text, mentions: Sequence[Mention], match: MatchContext, trigger: Trigger | None
) -> list[GuardrailViolation]:
    """At a momentum shift or a dangerous spell, the trigger team is the one on top."""
    if trigger is None or trigger.kind not in DIRECTION_KINDS or trigger.team is None:
        return []
    top = match.team(trigger.team).name
    found: list[GuardrailViolation] = []
    for mention in mentions:
        side = _team_of(mention) if mention.kind == "team" else None
        if side is None:
            continue
        sentence = _sentence_of(text, mention.start)
        before = text.lower[max(sentence[0], mention.start - _LOOK_CHARS) : mention.start]
        after = text.lower[mention.end : sentence[1]]
        name = text.raw[mention.start : mention.end]
        pressed = bool(_PRESSED_BEFORE.search(before) or _PRESSED_AFTER.match(after))
        if _FAVOURS.search(before) and side != trigger.team:
            detail = f"the game tilts towards {top} at this moment, not {name}"
        elif pressed and side == trigger.team:
            detail = f"{top} are the side on top at this moment, not the one under pressure"
        else:
            continue
        found.append(GuardrailViolation(code="WRONG_ATTRIBUTION", detail=detail, span=name))
    return found


def _token_kind(token: str, after_digit: bool) -> Literal["en", "vi", "skip"]:
    if not token.isascii():
        return "vi"
    if after_digit or len(token) == 1 or any(ch.isupper() for ch in token[1:]):
        return "skip"  # "49th", "xG", "VAR", "FC"
    low = token.lower()
    if token[0].isupper() and low not in _CAPITAL_EN:
        return "skip" if not _VI_SYLLABLE.fullmatch(low) else "vi"  # a name
    if low in _NEUTRAL_ASCII:
        return "skip"
    return "vi" if _VI_SYLLABLE.fullmatch(low) else "en"


def english_run_violations(
    text: Text, masked: Sequence[tuple[int, int]]
) -> list[GuardrailViolation]:
    """Two English words in a row inside a Vietnamese line ("đi off target")."""
    raw = text.raw
    runs: list[tuple[int, int]] = []
    run: list[tuple[int, int]] = []
    for word in _WORD.finditer(raw):
        start, end = word.start(), word.end()
        if any(a <= start < b for a, b in masked):
            continue
        kind = _token_kind(word.group(0), start > 0 and raw[start - 1].isdigit())
        if kind == "skip":
            continue
        if kind == "en":
            run.append((start, end))
            continue
        if len(run) >= MIN_ENGLISH_RUN:
            runs.append((run[0][0], run[-1][1]))
        run = []
    if len(run) >= MIN_ENGLISH_RUN:
        runs.append((run[0][0], run[-1][1]))
    return [
        GuardrailViolation(
            code="WRONG_LANGUAGE",
            detail=f"English words in a Vietnamese line: {raw[a:b]!r}",
            span=raw[a:b],
        )
        for a, b in runs
    ]


def _one_edit_apart(a: str, b: str) -> bool:
    """One letter added, dropped or changed ("Haror" / "Harbour" / "Harbors" vs "Harbor")."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def misspelled_team_violations(
    text: Text, mentions: Sequence[Mention], match: MatchContext
) -> list[GuardrailViolation]:
    """A capitalised word one letter away from a word of a team name."""
    words = {
        word.lower(): team.name
        for team in match.teams
        for alias in team.aliases
        for word in alias.split()
        if len(word) >= MIN_NAME_WORD and word.isalpha()
    }
    found: list[GuardrailViolation] = []
    for word in _WORD.finditer(text.raw):
        token = word.group(0)
        if len(token) < MIN_NAME_WORD - 1 or not token[0].isupper() or token.isupper():
            continue
        if any(m.start <= word.start() < m.end for m in mentions):
            continue
        near = sorted({name for w, name in words.items() if _one_edit_apart(token.lower(), w)})
        if near:
            found.append(
                GuardrailViolation(
                    code="WRONG_ENTITY",
                    detail=f"{token!r} is not a team name here; write {near[0]}",
                    span=token,
                )
            )
    return found
