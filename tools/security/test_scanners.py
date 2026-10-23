"""Tests of the secret and compliance scanners (docs/day6-design.md section 7.3).

Fake secrets are put together while the tests run, so that this file itself holds none (GitHub
push protection and gitleaks read it too). Real names come from real_names.txt, the only file
that lists them.
"""

import subprocess
from pathlib import Path

import pytest
import scan_compliance as compliance
import scan_secrets as secrets

FAKE = {
    "azure-storage-key": "DefaultEndpointsProtocol=https;Account" + "Key=" + "Ab3/" * 22 + "==",
    "azure-sas": "https://acct.blob.core.windows.net/c/a.mp3?sv=2024-01-01&se=2026-11-01&sp=r&s"
    + "ig=" + "AbCdEf0123456789AbCdEf0123%2B%3D",
    "azure-cognitive-key": "SPEECH" + "_KEY=" + "0123456789abcdef" * 2,
    "appinsights-connection": "Instrumentation" + "Key=" + "12345678-1234-1234-1234-123456789abc",
    "openai-key": 'client(api="s' + "k-" + "A1b2C3d4" * 5 + '")',
    "github-token": "gh" + "p_" + "A1b2C3d4E5" * 4,
    "private-key": "-" * 5 + "BEGIN RSA " + "PRIVATE KEY" + "-" * 5,
    "jwt": "Bearer ey" + "J" + "hbGciOiJIUzI1NiJ9.ey" + "J"
    + "zdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123_-xyzXYZ789",
    "generic-assignment": "pass" + 'word = "' + "Zx8!qL2#vN5@rT9$" + '"',
}  # fmt: skip
NAMES = compliance.load_names()


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.test", *args],
        cwd=root, capture_output=True, check=True, text=True,
    ).stdout  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("synthetic\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "start")
    return tmp_path


# secrets ------------------------------------------------------------------------------------


@pytest.mark.parametrize("rule", sorted(FAKE))
def test_each_rule_finds_its_secret(rule: str) -> None:
    found = secrets.scan_text(f"x = 1\n{FAKE[rule]}\n", "a.txt", "worktree")
    assert [(f.rule, f.line) for f in found] == [(rule, 2)]


def test_every_rule_has_a_case() -> None:
    assert {rule.name for rule in secrets.RULES} == set(FAKE)


def test_a_finding_is_redacted() -> None:
    key = "gh" + "p_" + "Z9y8X7w6V5" * 4
    (finding,) = secrets.scan_text(f"token: {key}", "a.txt", "worktree")
    assert finding.snippet == f"{key[:4]}...{key[-4:]}" and key not in finding.snippet
    assert len(finding.fingerprint) == 64


def test_the_generic_rule_needs_a_random_value() -> None:
    plain = "pass" + 'word = "' + "a" * 20 + '"'
    words = "to" + 'ken = "' + "the token of a session" + '"'  # a sentence, not a secret
    assert secrets.scan_text(plain, "a.txt", "worktree") == []
    assert secrets.scan_text(words, "a.txt", "worktree") == []


def test_a_sas_signature_needs_its_url() -> None:
    alone = "?s" + "ig=" + "AbCdEf0123456789AbCdEf0123"
    assert secrets.scan_text(alone, "a.txt", "worktree") == []


def test_the_history_keeps_a_deleted_secret(repo: Path) -> None:
    (repo / "config.py").write_text(FAKE["openai-key"] + "\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add a key")
    added = git(repo, "rev-parse", "--short", "HEAD").strip()
    (repo / "config.py").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "remove it")
    assert secrets.scan_worktree(repo)[0] == []
    (finding,) = secrets.scan_history(repo)[0]
    assert (finding.rule, finding.path, finding.commit) == ("openai-key", "config.py", added)
    assert secrets.main(["--history"], root=repo) == 1


def test_new_files_are_scanned_before_any_commit(repo: Path) -> None:
    (repo / "notes.txt").write_text(FAKE["jwt"], encoding="utf-8")
    assert [f.rule for f in secrets.scan_worktree(repo)[0]] == ["jwt"]
    (repo / ".gitignore").write_text("notes.txt\n", encoding="utf-8")  # ignored: not shipped
    assert secrets.scan_worktree(repo)[0] == []


def test_binary_and_large_files_are_skipped(repo: Path) -> None:
    (repo / "image.bin").write_bytes(b"\0\x01" + FAKE["openai-key"].encode())
    (repo / "big.txt").write_text("x" * (secrets.MAX_BYTES + 1), encoding="utf-8")
    findings, skipped = secrets.scan_worktree(repo)
    assert findings == [] and sorted(skipped) == ["big.txt", "image.bin"]


def test_a_committed_env_file_is_a_finding(repo: Path) -> None:
    for name in (".env.example", "backend/.env"):
        (repo / name).parent.mkdir(exist_ok=True)
        (repo / name).write_text("SPEECH_KEY=\n", encoding="utf-8")
    git(repo, "add", "-f", ".")
    git(repo, "commit", "-q", "-m", "env")
    assert [(f.rule, f.path) for f in secrets.committed_env_files(repo)] == [
        ("committed-env", "backend/.env")
    ]


def test_the_build_is_scanned(tmp_path: Path) -> None:
    chunk = tmp_path / "frontend" / "out" / "_next" / "app.js"
    chunk.parent.mkdir(parents=True)
    chunk.write_text(f"const k='{FAKE['azure-cognitive-key']}';", encoding="utf-8")
    assert [f.where for f in secrets.scan_build(tmp_path)] == ["build"]


def test_the_allowlist_allows_one_fingerprint_only(repo: Path) -> None:
    for name, rule in (("a.py", "github-token"), ("b.py", "openai-key")):
        (repo / name).write_text(FAKE[rule], encoding="utf-8")
    (allowed,) = [f for f in secrets.scan_worktree(repo)[0] if f.path == "a.py"]
    allowlist = repo / "allowlist.toml"
    allowlist.write_text(
        f'[[allow]]\nrule = "github-token"\npath = "*.py"\n'
        f'fingerprint = "{allowed.fingerprint[:16]}"\nreason = "a test"\n',
        encoding="utf-8",
    )
    entries = secrets.load_allowlist(allowlist)
    assert secrets.is_allowed(allowed, entries)
    assert secrets.main(["--worktree", "--allowlist", str(allowlist)], root=repo) == 1  # b.py
    (repo / "b.py").unlink()
    assert secrets.main(["--worktree", "--allowlist", str(allowlist)], root=repo) == 0


@pytest.mark.parametrize(
    "entry",
    [
        'rule = "jwt"\npath = "*"\nfingerprint = "0123456789abcdef"\nreason = " "',
        'rule = "jwt"\npath = "*"\nfingerprint = "0123"\nreason = "too short: allows anything"',
        'rule = "nope"\npath = "*"\nfingerprint = "0123456789abcdef"\nreason = "unknown rule"',
        'rule = "jwt"\npath = ""\nfingerprint = "0123456789abcdef"\nreason = "no path"',
    ],
)
def test_a_bad_allowlist_entry_is_an_error(tmp_path: Path, entry: str) -> None:
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(f"[[allow]]\n{entry}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        secrets.load_allowlist(allowlist)


def test_the_scanners_hold_no_secret() -> None:
    for path in (Path(secrets.__file__), Path(compliance.__file__), Path(__file__)):
        assert secrets.scan_text(path.read_text(encoding="utf-8"), path.name, "worktree") == []


def test_the_repository_is_clean() -> None:
    assert secrets.main(["--worktree", "--history"]) == 0


# compliance ---------------------------------------------------------------------------------


def test_the_names_file() -> None:
    sections = {"brand", "disclaimer", "club", "short", "stadium", "player", "manager", "referee"}
    assert sections <= set(NAMES)
    assert len(NAMES["player"]) >= 150
    for section, names in NAMES.items():
        assert len(names) == len(set(names)), section


def test_a_line_outside_a_section_is_refused(tmp_path: Path) -> None:
    names = tmp_path / "names.txt"
    names.write_text("Somebody\n[player]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not under a"):
        compliance.load_names(names)


def test_folding() -> None:
    assert compliance.fold("Ødegaard, Gündoğan!") == "Odegaard Gundogan "
    assert compliance.fold("N'Golo Kanté").split() == ["N", "Golo", "Kante"]
    assert compliance.fold("Đỗ Hải Phong") == "Do Hai Phong"


def test_a_real_name_is_found_with_or_without_accents() -> None:
    matcher = compliance.Matcher(NAMES)
    accented = next(n for n in NAMES["player"] if compliance.fold(n) != n and " " in n)
    for written in (accented, compliance.fold(accented).upper(), accented.replace(" ", "  ")):
        assert [s for s, _ in matcher.find(f'name: "{written}",')] == ["player"]
    club = next(n for n in NAMES["club"] if n.isalpha())
    assert matcher.find(f"{club}x and x{club}") == []  # whole words only


def test_short_names_keep_their_case() -> None:
    matcher = compliance.Matcher(NAMES)
    short = next(n for n in NAMES["short"] if n.isalpha() and n[0].isupper() and n[1:].islower())
    assert matcher.find(f"the {short} won") == [("short", short)]
    assert matcher.find(f"the {short.lower()} won") == []


def test_a_brand_only_inside_a_disclaimer() -> None:
    matcher = compliance.Matcher(NAMES)
    brand = NAMES["brand"][0]
    for disclaimer in NAMES["disclaimer"]:
        assert matcher.find(f'"Synthetic data. {disclaimer}."') == []
    assert matcher.find(f"Live from the {brand}") == [("brand", brand.lower())]


def test_a_shared_surname_is_a_warning_not_an_error() -> None:
    surname = NAMES["player"][0].split()[-1]
    (warning,) = compliance.surname_warnings(NAMES, [f"Kai {surname}"])
    assert warning.level == "warning" and compliance.fold(surname).lower() in warning.detail
    assert compliance.surname_warnings(NAMES, [f"{surname} Brandvold"]) == []  # a first name


def test_images_and_logo_links() -> None:
    files = ["frontend/public/crest.png", "frontend/src/app/icon.svg", "backend/x.png"]
    assert [f.path for f in compliance.image_findings(files)] == ["frontend/public/crest.png"]
    matcher = compliance.Matcher(NAMES)
    lines = 'src="https://upload.wikimedia.org/a.svg"\nfetch("https://cdn.example.test/t.png?v=1")'
    found = compliance.scan_text(matcher, lines, "a.tsx")
    assert [(f.kind, f.line) for f in found] == [("logo-url", 1), ("logo-url", 2)]
    schema_id = '"$id": "https://pundit.example/schemas/x.json"'
    assert compliance.scan_text(matcher, schema_id, "s") == []


def test_the_scope() -> None:
    inside = ["backend/src/pundit/x.py", "data/matches/a.jsonl", "frontend/src/app/page.tsx",
              "packages/schemas/a.schema.json", "backend/tests/golden/a.json",
              "frontend/tests/fixtures/a.jsonl", "backend/evals/claims.json"]  # fmt: skip
    outside = ["docs/day6-design.md", "claude.md", "README.md", "backend/tests/test_x.py",
               "frontend/tests/e2e/a.spec.ts", "tools/security/real_names.txt"]  # fmt: skip
    assert all(compliance.in_scope(p) for p in inside)
    assert not any(compliance.in_scope(p) for p in outside)


def test_the_product_is_compliant() -> None:
    findings, read = compliance.scan()
    assert read > 100
    assert [f for f in findings if f.level == "error"] == []
