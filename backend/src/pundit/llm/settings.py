"""Configuration from environment variables (docs/day2-design.md section 8.4).

`USE_MOCK_LLM=true` (the default) ignores every provider setting. With a real provider, a
missing variable stops the program at start-up with a clear message instead of failing on the
first claim. Secrets are `SecretStr`: they never appear in `repr`, logs or traces.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pundit.domain.errors import PunditError
from pundit.llm.faults import FaultPlan

Provider = Literal["mock", "github", "foundry"]
DEFAULT_GITHUB_ENDPOINT = "https://models.github.ai/inference"
DEFAULT_GITHUB_MODEL = "openai/gpt-4.1-mini"
DEFAULT_CORS = "http://localhost:3000"
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
FALSE_WORDS = frozenset({"0", "false", "no", "off"})


class ConfigError(PunditError):
    """The environment does not describe a usable configuration."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    use_mock_llm: bool = True
    llm_provider: Literal["github", "foundry"] | None = None
    github_token: SecretStr | None = None
    github_models_endpoint: str = DEFAULT_GITHUB_ENDPOINT
    github_models_model: str = DEFAULT_GITHUB_MODEL
    foundry_endpoint: str | None = None
    foundry_model_deployment: str | None = None
    foundry_api_key: SecretStr | None = (
        None  # dev only; Entra ID (az login / managed identity) otherwise
    )
    llm_timeout_s: float = Field(default=4.0, gt=0)
    claim_budget_s: float = Field(default=8.0, gt=0)
    mock_llm_faults: str = ""
    mock_llm_latency_ms: float = Field(default=0.0, ge=0)
    cors_origins: tuple[str, ...] = (DEFAULT_CORS,)

    @property
    def provider(self) -> Provider:
        if self.use_mock_llm or self.llm_provider is None:
            return "mock"
        return self.llm_provider

    @property
    def model_name(self) -> str:
        if self.provider == "github":
            return self.github_models_model
        if self.provider == "foundry":
            return self.foundry_model_deployment or "foundry"
        return "mock"

    def describe(self) -> dict[str, object]:
        """Safe summary for /healthz and logs (no secret, no token)."""
        return {
            "llm_provider": self.provider,
            "model": self.model_name,
            "llm_timeout_s": self.llm_timeout_s,
            "claim_budget_s": self.claim_budget_s,
            "mock_llm_faults": self.mock_llm_faults or None,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Read and check the configuration. Raises ConfigError with every problem found."""
        source = dict(os.environ if env is None else env)

        def get(name: str) -> str | None:
            value = source.get(name)
            return value.strip() if value is not None and value.strip() else None

        problems: list[str] = []

        def flag(name: str, default: bool) -> bool:
            value = get(name)
            if value is None:
                return default
            if value.lower() in TRUE_WORDS:
                return True
            if value.lower() in FALSE_WORDS:
                return False
            problems.append(f"{name} must be true or false, got {value!r}")
            return default

        def number(name: str, default: float) -> float:
            value = get(name)
            if value is None:
                return default
            try:
                parsed = float(value)
            except ValueError:
                problems.append(f"{name} must be a number, got {value!r}")
                return default
            if parsed < 0 or (parsed == 0 and name != "MOCK_LLM_LATENCY_MS"):
                problems.append(f"{name} must be > 0, got {value!r}")
                return default
            return parsed

        use_mock = flag("USE_MOCK_LLM", True)
        provider = get("LLM_PROVIDER")
        if provider is not None:
            provider = provider.lower()
        if not use_mock:
            if provider not in ("github", "foundry"):
                problems.append(
                    "USE_MOCK_LLM=false needs LLM_PROVIDER=github or LLM_PROVIDER=foundry"
                )
            elif provider == "github" and get("GITHUB_TOKEN") is None:
                problems.append(
                    "LLM_PROVIDER=github needs GITHUB_TOKEN (a token with GitHub Models access)"
                )
            elif provider == "foundry":
                for name in ("FOUNDRY_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT"):
                    if get(name) is None:
                        problems.append(f"LLM_PROVIDER=foundry needs {name}")
        faults = get("MOCK_LLM_FAULTS") or ""
        if faults:
            try:
                FaultPlan.parse(faults)
            except ValueError as exc:
                problems.append(f"MOCK_LLM_FAULTS: {exc}")
        settings_values: dict[str, object] = {
            "use_mock_llm": use_mock,
            "llm_provider": provider if provider in ("github", "foundry") else None,
            "github_token": SecretStr(token) if (token := get("GITHUB_TOKEN")) else None,
            "github_models_endpoint": get("GITHUB_MODELS_ENDPOINT") or DEFAULT_GITHUB_ENDPOINT,
            "github_models_model": get("GITHUB_MODELS_MODEL") or DEFAULT_GITHUB_MODEL,
            "foundry_endpoint": get("FOUNDRY_ENDPOINT"),
            "foundry_model_deployment": get("FOUNDRY_MODEL_DEPLOYMENT"),
            "foundry_api_key": SecretStr(key) if (key := get("FOUNDRY_API_KEY")) else None,
            "llm_timeout_s": number("LLM_TIMEOUT_S", 4.0),
            "claim_budget_s": number("CLAIM_BUDGET_S", 8.0),
            "mock_llm_faults": faults,
            "mock_llm_latency_ms": number("MOCK_LLM_LATENCY_MS", 0.0),
            "cors_origins": tuple(
                o.strip() for o in (get("CORS_ORIGINS") or DEFAULT_CORS).split(",") if o.strip()
            ),
        }
        if problems:
            raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(problems))
        return cls.model_validate(settings_values)


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (comments and blank lines ignored, quotes stripped)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name] = value
    return values


def load_settings(env_file: Path | None = None) -> Settings:
    """Environment variables win over the .env file (which is never committed)."""
    merged = read_env_file(env_file) if env_file is not None else {}
    merged.update(os.environ)
    return Settings.from_env(merged)
