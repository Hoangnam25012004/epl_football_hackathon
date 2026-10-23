"""Official Rules compliance scan (docs/day6-design.md section 7.5): no real club, stadium,
player, manager or referee name and no logo in the product, which uses synthetic data only.

Where: backend/src, data/, frontend/src, frontend/public, packages/schemas and the JSON fixtures
of the tests and the eval. Not docs/, claude.md, checklist.md or the README, where the name of the
hackathon is allowed. Names come from real_names.txt; a match is a whole word, with accents and
punctuation folded away (Ødegaard = Odegaard).

An error makes the exit code 1. A warning, a fictional name of the rosters that shares a surname
with a real person, is printed for a human to look at. Standard library only.

    python tools/security/scan_compliance.py
"""

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = Path(__file__).with_name("real_names.txt")
ROSTERS = "backend/src/pundit/data/rosters.json"
SCOPE = ("backend/src/", "data/", "frontend/src/", "frontend/public/", "packages/schemas/")
FIXTURES = ("backend/tests/", "frontend/tests/", "backend/evals/")
FIXTURE_SUFFIXES = (".json", ".jsonl")
TEXT_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".jsonl", ".css", ".html",
                 ".md", ".txt", ".svg", ".toml", ".yaml", ".yml", ".xml")  # fmt: skip
IMAGE_DIRS = ("frontend/src/", "frontend/public/")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif", ".bmp",
                  ".tif", ".tiff")  # fmt: skip
ALLOWED_IMAGES = frozenset({"frontend/src/app/icon.svg"})  # the app's own icon, drawn for it
LOGO_HOSTS = ("premierleague.com", "upload.wikimedia.org", "logo.clearbit.com", "football-data.org",
              "api-sports.io", "seeklogo.com", "logos-world.net", "brandfetch", "fotmob.com",
              "sofascore", "transfermarkt", "1000logos", "footylogos", "footballlogos")  # fmt: skip
URL = re.compile(r"https?://[^\s'\"<>)\]]+", re.IGNORECASE)
IMAGE_URL = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|ico|avif)(?:[?#]|$)", re.IGNORECASE)
CASED = frozenset({"short"})  # sections matched with their case
PEOPLE = ("player", "manager", "referee")
SPECIAL = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss",
                         "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "\u0131": "i", "ð": "d", "Ð": "D",
                         "þ": "th", "Þ": "Th"})  # fmt: skip


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    kind: str  # a section of real_names.txt, "image", "logo-url" or "surname"
    path: str
    line: int
    detail: str


def fold(text: str) -> str:
    """Accents off and every run of other characters one space, case kept."""
    plain = unicodedata.normalize("NFKD", text.translate(SPECIAL))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^0-9A-Za-z]+", " ", plain)


def load_names(path: Path = NAMES) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], [])
        elif current is None:
            raise ValueError(f"{path.name}: {line!r} is not under a [section]")
        else:
            current.append(line)
    return sections


def _words(names: Iterable[str]) -> re.Pattern[str] | None:
    alternatives = sorted({re.escape(n) for n in names}, key=len, reverse=True)  # longest first
    if not alternatives:
        return None
    return re.compile(r"(?<![0-9A-Za-z])(?:" + "|".join(alternatives) + r")(?![0-9A-Za-z])")


class Matcher:
    """Every real name of the list, found in one pass over a line."""

    def __init__(self, sections: dict[str, list[str]]) -> None:
        self.cased: dict[str, str] = {}  # folded name -> section
        self.uncased: dict[str, str] = {}  # folded lower-case name -> section
        for section, names in sections.items():
            if section == "disclaimer":
                continue
            for name in names:
                key = fold(name).strip()
                if section in CASED:
                    self.cased[key] = section
                else:
                    self.uncased[key.lower()] = section
        self.disclaimers = _words(fold(p).strip().lower() for p in sections.get("disclaimer", ()))
        self._cased = _words(self.cased)
        self._uncased = _words(self.uncased)

    def find(self, line: str) -> list[tuple[str, str]]:
        """(section, name) of each real name in the line; a brand inside a disclaimer is not one."""
        folded = fold(line)
        lower = folded.lower()
        allowed = [m.span() for m in self.disclaimers.finditer(lower)] if self.disclaimers else []
        found: list[tuple[str, str]] = []
        for pattern, text, kinds in ((self._uncased, lower, self.uncased),
                                     (self._cased, folded, self.cased)):  # fmt: skip
            for match in pattern.finditer(text) if pattern else ():
                section = kinds[match.group(0)]
                inside = any(a <= match.start() and match.end() <= b for a, b in allowed)
                if not (section == "brand" and inside):
                    found.append((section, match.group(0)))
        return found


def scan_text(matcher: Matcher, text: str, path: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for section, name in matcher.find(line):
            findings.append(Finding("error", section, path, number, f"real name {name!r}"))
        for url in URL.findall(line):
            if any(host in url.lower() for host in LOGO_HOSTS) or IMAGE_URL.search(url):
                findings.append(Finding("error", "logo-url", path, number, url))
    return findings


def image_findings(files: Iterable[str]) -> list[Finding]:
    return [
        Finding("error", "image", path, 0, "an image file in the app: only its own icon may ship")
        for path in files
        if path.startswith(IMAGE_DIRS)
        and path.lower().endswith(IMAGE_SUFFIXES)
        and path not in ALLOWED_IMAGES
    ]


def roster_people(root: Path = ROOT) -> list[str]:
    path = root / ROSTERS
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [p["name"] for t in data["teams"] for p in t["players"]] + list(data["referees"])


def surname_warnings(sections: dict[str, list[str]], people: Iterable[str]) -> list[Finding]:
    """A fictional person whose name (past the first word) holds a real person's surname."""
    surnames: dict[str, str] = {}
    for section in PEOPLE:
        for name in sections.get(section, ()):
            surnames.setdefault(fold(name).lower().split()[-1], name)
    warnings: list[Finding] = []
    for person in people:
        words = fold(person).lower().split()
        for word in words[1:] or words:
            if word in surnames:
                detail = f"{person!r} shares {word!r} with the real {surnames[word]!r}"
                warnings.append(Finding("warning", "surname", ROSTERS, 0, detail))
    return warnings


def files_of(root: Path = ROOT) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    ).stdout  # fmt: skip
    return sorted(p for p in out.decode("utf-8").split("\0") if p and (root / p).is_file())


def in_scope(path: str) -> bool:
    if path.startswith(SCOPE):
        return path.lower().endswith(TEXT_SUFFIXES)
    return path.startswith(FIXTURES) and path.lower().endswith(FIXTURE_SUFFIXES)


def scan(root: Path = ROOT, names: Path = NAMES) -> tuple[list[Finding], int]:
    """The findings and the number of files read."""
    sections = load_names(names)
    matcher = Matcher(sections)
    files = files_of(root)
    findings = image_findings(files)
    scoped = [p for p in files if in_scope(p)]
    for path in scoped:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        findings += scan_text(matcher, text, path)
    findings += surname_warnings(sections, roster_people(root))
    return findings, len(scoped)


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--names", type=Path, default=NAMES)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[attr-defined]
    findings, read = scan(root, args.names)
    for f in findings:
        place = f"{f.path}:{f.line}" if f.line else f.path
        print(f"{f.level.upper():7}  {f.kind:9} {place}  {f.detail}")
    errors = sum(f.level == "error" for f in findings)
    print(f"compliance scan: {read} files read, {errors} errors, {len(findings) - errors} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
