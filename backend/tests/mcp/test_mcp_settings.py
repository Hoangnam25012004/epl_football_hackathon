"""Day 6 configuration (docs/day6-design.md section 2.4) and who a client is (section 3.7)."""

from pathlib import Path

import pytest

from pundit.domain.clients import UNKNOWN, client_ip
from pundit.llm.settings import ConfigError, Settings, read_env_file


def test_mcp_defaults() -> None:
    settings = Settings.from_env({})
    assert settings.mcp_enabled is True
    assert settings.mcp_allowed_hosts == ("127.0.0.1", "localhost")
    assert settings.mcp_rate_per_minute == 60
    assert settings.trust_proxy is False
    assert settings.describe()["mcp"] == {
        "enabled": True,
        "allowed_hosts": ["127.0.0.1", "localhost"],
        "rate_per_minute": 60,
    }


def test_mcp_from_the_environment() -> None:
    settings = Settings.from_env(
        {
            "MCP_ENABLED": "false",
            "MCP_ALLOWED_HOSTS": " Pundit.Example.io , localhost,, pundit.example.io,[::1] ",
            "MCP_RATE_PER_MINUTE": "120",
            "TRUST_PROXY": "yes",
        }
    )
    assert settings.mcp_enabled is False
    assert settings.mcp_allowed_hosts == ("pundit.example.io", "localhost", "[::1]")
    assert (settings.mcp_rate_per_minute, settings.trust_proxy) == (120, True)


def test_an_empty_host_list_keeps_the_defaults() -> None:
    assert Settings.from_env({"MCP_ALLOWED_HOSTS": " , "}).mcp_allowed_hosts == (
        "127.0.0.1",
        "localhost",
    )


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MCP_ENABLED": "maybe"}, "MCP_ENABLED"),
        ({"TRUST_PROXY": "sometimes"}, "TRUST_PROXY"),
        ({"MCP_RATE_PER_MINUTE": "0"}, "MCP_RATE_PER_MINUTE"),
        ({"MCP_RATE_PER_MINUTE": "lots"}, "MCP_RATE_PER_MINUTE"),
        ({"MCP_ALLOWED_HOSTS": "https://pundit.example.io"}, "without scheme, port or path"),
        ({"MCP_ALLOWED_HOSTS": "localhost:8000"}, "without scheme, port or path"),
        ({"MCP_ALLOWED_HOSTS": "pundit.example.io/mcp"}, "without scheme, port or path"),
    ],
)
def test_a_bad_mcp_configuration_fails_fast(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_the_env_example_names_the_day_6_variables() -> None:
    values = read_env_file(Path(__file__).resolve().parents[2] / ".env.example")
    for name in ("MCP_ENABLED", "MCP_ALLOWED_HOSTS", "MCP_RATE_PER_MINUTE", "TRUST_PROXY"):
        assert name in values, name
    settings = Settings.from_env(values)
    assert (settings.mcp_enabled, settings.trust_proxy) == (True, False)


@pytest.mark.parametrize(
    ("peer", "forwarded", "trust", "expected"),
    [
        ("10.0.0.5", None, False, "10.0.0.5"),
        ("10.0.0.5", "203.0.113.9", False, "10.0.0.5"),  # not trusted: the header is ignored
        ("10.0.0.5", "203.0.113.9", True, "203.0.113.9"),
        ("10.0.0.5", "198.51.100.66, 203.0.113.9", True, "203.0.113.9"),  # the proxy's hop
        ("10.0.0.5", " , ", True, "10.0.0.5"),
        (None, None, False, UNKNOWN),
        (None, "203.0.113.9", True, "203.0.113.9"),
    ],
)
def test_client_ip(peer: str | None, forwarded: str | None, trust: bool, expected: str) -> None:
    assert client_ip(peer, forwarded, trust_proxy=trust) == expected
