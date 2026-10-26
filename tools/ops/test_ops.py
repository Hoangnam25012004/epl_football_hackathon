"""The deploy of the Space (docs/day7-design.md sections 2.2-2.3) without the network: the files
written to the Space, the stages waited for, the smoke checks, and the Space's defaults read by
the app's own settings, so a typo in env.space fails here and not on the public demo."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "space"))

import deploy_space
import smoke_space
import warm_model
from pundit.llm.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "ab" * 32
IMAGE = f"ghcr.io/owner/pundit-copilot-space@{DIGEST}"


def env_space() -> dict[str, str]:
    """deploy/space/env.space as start.sh reads it: NAME=value lines; comments, blanks skipped."""
    values = {}
    for line in (ROOT / "deploy/space/env.space").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, _, value = line.partition("=")
            values[name] = value
    return values


def test_the_space_defaults_are_a_valid_configuration() -> None:
    host = "owner-pundit-copilot.hf.space"
    env = {**env_space(), "LLM_MODEL": "qwen2.5:1.5b",  # from the image
           "KEEPALIVE_URL": f"https://{host}/healthz", "CORS_ORIGINS": f"https://{host}",
           "MCP_ALLOWED_HOSTS": f"{host},127.0.0.1,localhost"}  # from SPACE_HOST  # fmt: skip
    settings = Settings.from_env(env)
    assert (settings.provider, settings.model_name) == ("ollama", "qwen2.5:1.5b")
    assert settings.llm_live_scope == "claims" and not settings.studio_uses_model
    assert settings.llm_timeout_s <= settings.claim_budget_s <= 60  # a claim never hangs
    assert settings.claim_progressive and settings.model_queue_max >= 1
    assert settings.llm_timeout_s <= settings.model_pass_budget_s
    assert settings.tts_provider == "browser" and settings.stt_provider == "off"
    assert settings.trust_proxy and settings.keepalive_url == f"https://{host}/healthz"
    assert host in settings.mcp_allowed_hosts


def test_env_space_holds_no_secret_and_no_host() -> None:
    names = set(env_space())
    assert not {n for n in names if any(w in n for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
    assert not names & {"KEEPALIVE_URL", "CORS_ORIGINS", "MCP_ALLOWED_HOSTS", "LLM_MODEL"}


@pytest.mark.parametrize(
    ("cpu_max", "cores", "threads"),
    [
        ("200000 100000\n", 16, 2),  # the Space: 2 vCPUs on a host with many cores
        ("150000 100000", 16, 2),  # a part of a CPU counts as a whole one
        ("50000 100000", 16, 1),
        ("max 100000", 16, 16),  # no quota
        ("800000 100000", 4, 4),  # never more threads than cores
        (None, 8, 8),  # no cgroup v2 file
    ],
)
def test_the_model_gets_one_thread_per_cpu_of_the_quota(
    cpu_max: str | None, cores: int, threads: int
) -> None:
    assert warm_model.cpu_quota(cpu_max, cores) == threads


def test_the_space_files_pin_the_image_by_digest() -> None:
    files = deploy_space.space_files(IMAGE, "https://github.com/owner/repo")
    assert files["Dockerfile"].decode().splitlines()[-1] == f"FROM {IMAGE}"
    readme = files["README.md"].decode()
    header = readme.split("---")[1]
    assert "sdk: docker" in header and "app_port: 8000" in header
    assert "https://github.com/owner/repo" in readme and IMAGE in readme
    assert "{" not in readme  # every placeholder filled
    assert "Synthetic data only" in readme and "Not affiliated with the Premier League" in readme


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/owner/pundit-copilot-space:latest",  # a tag the Space could cache
        "ghcr.io/owner/pundit-copilot-space@sha256:abc",
        "ghcr.io/Owner/x@" + DIGEST,  # registries want lower case
        "",
    ],
)
def test_an_image_without_a_full_digest_is_refused(image: str) -> None:
    with pytest.raises(deploy_space.DeployError, match="pinned by digest"):
        deploy_space.check_image(image)


def test_the_space_name_is_owner_slash_name() -> None:
    assert deploy_space.check_space("hnam2501/pundit-copilot") == "hnam2501/pundit-copilot"
    for wrong in ("pundit-copilot", "https://huggingface.co/spaces/a/b", "a/b/c"):
        with pytest.raises(deploy_space.DeployError):
            deploy_space.check_space(wrong)


@pytest.mark.parametrize(
    ("stage", "verdict"),
    [
        ("RUNNING", "done"),
        ("BUILDING", "wait"),
        ("APP_STARTING", "wait"),
        ("RUNNING_BUILDING", "wait"),  # the old container answers while the new one builds
        ("BUILD_ERROR", "failed"),
        ("RUNTIME_ERROR", "failed"),
        ("CONFIG_ERROR", "failed"),
        ("NO_APP_FILE", "failed"),
        ("PAUSED", "failed"),  # waiting never ends it: seen with "Quota exceeded ... limit=0"
    ],
)
def test_the_stages_waited_for(stage: str, verdict: str) -> None:
    assert deploy_space.stage_verdict(stage) == verdict


def test_the_smoke_waits_for_the_expected_build() -> None:
    healthy = {"status": "ok", "synthetic": True, "build": "d8e1488"}
    assert smoke_space.check_health(healthy, "d8e1488") == []
    assert smoke_space.check_health(healthy, None) == []
    assert smoke_space.check_health({**healthy, "build": "0ab8de7"}, "d8e1488") == [
        "build '0ab8de7', expected 'd8e1488'"
    ]
    assert smoke_space.check_health({"status": "ok"}, None) == ["synthetic is not true"]
