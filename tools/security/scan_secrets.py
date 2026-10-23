"""Secret scan of the repository (docs/day6-design.md section 7.1-7.2).

Where: every blob of the git history (every ref, the stash included), the working tree (tracked
files and new ones), the frontend build (frontend/out: it goes into the Docker image) and any
.env file ever committed. A finding is printed with its rule, place, line and a redacted snippet;
one that the allowlist does not cover makes the exit code 1. Standard library only, so it runs
before anything is installed.

    python tools/security/scan_secrets.py --history --worktree --build

The patterns are built from pieces so that this file never matches itself.
"""

import argparse
import fnmatch
import hashlib
import math
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name("allowlist.toml")
MAX_BYTES = 2 * 1024 * 1024
BUILD_SUFFIXES = (".js", ".html", ".txt", ".json")
ENTROPY_MIN = 3.5
FINGERPRINT = re.compile(r"[0-9a-f]{16,64}")
_B = "-----"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    entropy: float | None = None  # the captured value must be this random (generic rule)
    together: re.Pattern[str] | None = None  # must also be on the same line (a SAS URL)


RULES = (
    Rule("azure-storage-key", re.compile("Account" + r"Key=[A-Za-z0-9+/]{80,}={0,2}")),
    Rule(
        "azure-sas",
        re.compile(r"[?&]" + "sig" + r"=[A-Za-z0-9%+/=]{20,}"),
        together=re.compile(r"[?&](?:se|sp)="),
    ),
    Rule(
        "azure-cognitive-key",
        re.compile(
            r"(?i)(?:SPEECH_KEY|FOUNDRY_API_KEY|api-key|Ocp-Apim-Subscription-Key)"
            r"[\"']?\s*[:=]\s*[\"']?([0-9a-f]{32})\b"
        ),
    ),
    Rule(
        "appinsights-connection",
        re.compile("Instrumentation" + r"Key=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"),
    ),
    Rule("openai-key", re.compile(r"\b" + "sk" + r"-[A-Za-z0-9_-]{32,}")),
    Rule(
        "github-token",
        re.compile(
            r"\b(?:ghp|gho|ghs|ghu|ghr)" + "_" + r"[A-Za-z0-9]{30,}"
            r"|\bgithub" + "_pat_" + r"[A-Za-z0-9_]{40,}"
        ),
    ),
    Rule("private-key", re.compile(_B + "BEGIN " + r"(?:[A-Z]+ )*" + "PRIVATE KEY" + _B)),
    Rule(
        "jwt",
        re.compile(
            r"\b"
            + "eyJ"
            + r"[A-Za-z0-9_-]{10,}\."
            + "eyJ"
            + r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
    ),
    Rule(
        "generic-assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]([^'\"\s]{16,})['\"]"
        ),
        entropy=ENTROPY_MIN,
    ),
)


@dataclass(frozen=True)
class Finding:
    rule: str
    where: str  # "worktree", "history", "build", "env"
    path: str
    line: int
    snippet: str  # redacted
    fingerprint: str  # sha256 of the matched text
    commit: str = ""


@dataclass(frozen=True)
class Allowed:
    rule: str
    path: str  # a glob
    fingerprint: str  # a sha256, or a prefix of one
    reason: str


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values()) if text else 0.0


def redact(text: str) -> str:
    return text if len(text) <= 8 else f"{text[:4]}...{text[-4:]}"


def scan_text(text: str, path: str, where: str, commit: str = "") -> list[Finding]:
    found: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                value = match.group(match.lastindex or 0)
                if rule.entropy is not None and entropy(value) < rule.entropy:
                    continue
                if rule.together is not None and not rule.together.search(line):
                    continue
                found.append(
                    Finding(
                        rule=rule.name,
                        where=where,
                        path=path,
                        line=number,
                        snippet=redact(match.group(0)),
                        fingerprint=hashlib.sha256(match.group(0).encode()).hexdigest(),
                        commit=commit,
                    )
                )
    return found


def decode(data: bytes) -> str | None:
    """Text, or None for a binary file (a NUL byte) or a file too large to scan."""
    if len(data) > MAX_BYTES or b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _git(*args: str, cwd: Path = ROOT, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=cwd, input=data, capture_output=True, check=True
    ).stdout


def worktree_files(root: Path = ROOT) -> list[str]:
    out = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z", cwd=root)
    return [p for p in out.decode("utf-8").split("\0") if p]


def scan_worktree(root: Path = ROOT) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    skipped: list[str] = []
    for relative in worktree_files(root):
        path = root / relative
        if not path.is_file():
            continue  # deleted but still in the index
        text = decode(path.read_bytes())
        if text is None:
            skipped.append(relative)
            continue
        findings += scan_text(text, relative, "worktree")
    return findings, skipped


def _blobs(root: Path) -> list[tuple[str, str]]:
    """(blob sha, a path it had) for every blob of every ref, each blob once."""
    out = _git("rev-list", "--objects", "--all", "--filter=object:type=blob", cwd=root)
    blobs: dict[str, str] = {}
    for line in out.decode("utf-8").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            blobs.setdefault(sha, path)
    return list(blobs.items())


def _contents(root: Path, shas: list[str]) -> Iterator[tuple[str, bytes]]:
    """(type, content) of each object, in order, streamed from one `git cat-file --batch`."""
    with tempfile.TemporaryFile() as names:
        names.write("".join(f"{sha}\n" for sha in shas).encode())
        names.seek(0)
        git = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=names,
                               stdout=subprocess.PIPE)  # fmt: skip
        assert git.stdout is not None
        with git:
            for _ in shas:
                header = git.stdout.readline().split()
                if len(header) < 3:  # "<sha> missing"
                    yield "missing", b""
                    continue
                data = git.stdout.read(int(header[2]))
                git.stdout.read(1)  # the newline after each content
                yield header[1].decode(), data


def scan_history(root: Path = ROOT) -> tuple[list[Finding], list[str]]:
    blobs = _blobs(root)
    findings: list[Finding] = []
    skipped: list[str] = []
    for (sha, path), (kind, data) in zip(
        blobs, _contents(root, [s for s, _ in blobs]), strict=True
    ):
        if kind != "blob":
            continue
        text = decode(data)
        if text is None:
            skipped.append(f"{path} ({sha[:8]})")
            continue
        found = scan_text(text, path, "history")
        if found:  # the oldest commit whose diff holds the blob is the one that added it
            commits = _git("log", "--all", "--format=%h", f"--find-object={sha}", cwd=root)
            added = commits.decode().split()[-1] if commits.strip() else ""
            findings += [_with_commit(finding, added) for finding in found]
    return findings, skipped


def _with_commit(finding: Finding, commit: str) -> Finding:
    return Finding(**{**finding.__dict__, "commit": commit})


def committed_env_files(root: Path = ROOT) -> list[Finding]:
    """Any .env (but .env.example) that a commit ever added."""
    out = _git("log", "--all", "--diff-filter=A", "--name-only", "--format=", cwd=root)
    found: list[Finding] = []
    for path in sorted({p for p in out.decode("utf-8").splitlines() if p}):
        name = path.rsplit("/", 1)[-1]
        if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
            found.append(Finding("committed-env", "env", path, 0, name, hashlib.sha256(
                path.encode()).hexdigest()))  # fmt: skip
    return found


def scan_build(root: Path = ROOT) -> list[Finding]:
    out = root / "frontend" / "out"
    findings: list[Finding] = []
    for path in sorted(out.rglob("*")) if out.is_dir() else ():
        if path.suffix in BUILD_SUFFIXES and path.is_file():
            text = decode(path.read_bytes())
            if text is not None:
                findings += scan_text(text, path.relative_to(root).as_posix(), "build")
    return findings


def load_allowlist(path: Path = ALLOWLIST) -> list[Allowed]:
    """The entries of allowlist.toml; a bad entry is an error, never a silent allow."""
    if not path.is_file():
        return []
    rules = {rule.name for rule in RULES} | {"committed-env"}
    allowed = []
    for entry in tomllib.loads(path.read_text(encoding="utf-8")).get("allow", []):
        reason = str(entry.get("reason", "")).strip()
        rule, glob, fingerprint = entry.get("rule"), entry.get("path"), entry.get("fingerprint", "")
        if not reason:
            raise ValueError(f"allowlist entry without a reason: {entry}")
        if rule not in rules or not glob:
            raise ValueError(f"allowlist entry with an unknown rule or no path: {entry}")
        if not FINGERPRINT.fullmatch(str(fingerprint)):  # a short prefix would allow anything
            raise ValueError(f"allowlist fingerprint must be 16 to 64 hex characters: {entry}")
        allowed.append(Allowed(rule, glob, fingerprint, reason))
    return allowed


def is_allowed(finding: Finding, allowlist: Iterable[Allowed]) -> bool:
    return any(
        a.rule == finding.rule
        and fnmatch.fnmatch(finding.path, a.path)
        and finding.fingerprint.startswith(a.fingerprint)
        for a in allowlist
    )


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--history", action="store_true", help="every blob of every ref")
    parser.add_argument("--worktree", action="store_true", help="tracked and new files")
    parser.add_argument("--build", action="store_true", help="frontend/out")
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[attr-defined]
    if not (args.history or args.worktree or args.build):
        args.history = args.worktree = args.build = True
    findings: list[Finding] = committed_env_files(root)
    skipped: list[str] = []
    for enabled, scan in ((args.worktree, scan_worktree), (args.history, scan_history)):
        if enabled:
            found, passed = scan(root)
            findings += found
            skipped += passed
    if args.build:
        findings += scan_build(root)
    allowlist = load_allowlist(args.allowlist)
    open_findings = [f for f in findings if not is_allowed(f, allowlist)]
    for f in findings:
        state = "allowed" if f not in open_findings else "FOUND"
        place = f"{f.path}:{f.line}" + (f" (commit {f.commit})" if f.commit else "")
        print(f"{state:7}  {f.rule:22} {f.where:8} {place}  {f.snippet}  "
              f"sha256:{f.fingerprint[:16]}")  # fmt: skip
    print(f"secret scan: {len(findings)} findings, {len(open_findings)} not allowlisted; "
          f"{len(skipped)} binary or large files skipped")  # fmt: skip
    return 1 if open_findings else 0


if __name__ == "__main__":
    sys.exit(main())
