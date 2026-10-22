"""Configuration from environment variables (docs/day2-design.md section 8.4).

`USE_MOCK_LLM=true` (the default) ignores every provider setting. With a real provider, a
missing variable stops the program at start-up with a clear message instead of failing on the
first claim. Secrets are `SecretStr`: they never appear in `repr`, logs or traces.

Providers:
- `ollama`: a local model through Ollama's OpenAI-compatible endpoint (development, no account);
- `openai`: any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_MODEL`, optional `LLM_API_KEY`);
- `foundry`: Microsoft Foundry, the target for the submission.
GitHub Models was retired on 2026-07-30 and is no longer a provider.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pundit.domain.errors import PunditError
from pundit.llm.faults import FaultPlan

Provider = Literal["mock", "ollama", "openai", "foundry"]
RealProvider = Literal["ollama", "openai", "foundry"]
REAL_PROVIDERS: tuple[RealProvider, ...] = ("ollama", "openai", "foundry")
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUTS = (4.0, 8.0)  # LLM_TIMEOUT_S, CLAIM_BUDGET_S for hosted models
# A local model on a laptop CPU needs far longer than a hosted one; still bounded.
PROVIDER_TIMEOUTS: dict[str, tuple[float, float]] = {"ollama": (90.0, 200.0)}
# Day 3 recaps are not real-time: the budget follows the provider (mock answers at once)
RECAP_BUDGETS: dict[str, float] = {"mock": 5.0, "ollama": 200.0}
DEFAULT_RECAP_BUDGET_S = 30.0
DEFAULT_CORS = "http://localhost:3000"
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
FALSE_WORDS = frozenset({"0", "false", "no", "off"})
RETIRED = {
    "github": "GitHub Models was retired on 2026-07-30: use LLM_PROVIDER=ollama (local) or foundry",
}


class ConfigError(PunditError):
    """The environment does not describe a usable configuration."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    use_mock_llm: bool = True
    llm_provider: RealProvider | None = None
    llm_base_url: str | None = None  # ollama (has a default) / openai (required)
    llm_model: str | None = None  # ollama (has a default) / openai (required)
    llm_api_key: SecretStr | None = None  # openai (optional; local servers need none)
    foundry_endpoint: str | None = None
    foundry_model_deployment: str | None = None
    # dev only; Entra ID (az login / managed identity) otherwise
    foundry_api_key: SecretStr | None = None
    llm_timeout_s: float = Field(default=DEFAULT_TIMEOUTS[0], gt=0)
    claim_budget_s: float = Field(default=DEFAULT_TIMEOUTS[1], gt=0)
    mock_llm_faults: str = ""
    mock_llm_latency_ms: float = Field(default=0.0, ge=0)
    cors_origins: tuple[str, ...] = (DEFAULT_CORS,)
    # Day 3 studio (docs/day3-design.md section 13.6)
    studio_enabled: bool = True
    studio_llm_min_importance: int = Field(default=1, ge=1, le=3)
    studio_tp_budget_s: float = Field(default=3.0, gt=0)
    studio_commentary_budget_s: float = Field(default=2.5, gt=0)
    studio_graphics_budget_s: float = Field(default=1.5, gt=0)
    studio_job_budget_s: float = Field(default=4.0, gt=0)
    recap_budget_s: float = Field(default=DEFAULT_RECAP_BUDGET_S, gt=0)
    studio_queue_size: int = Field(default=512, ge=8)
    studio_drop_at: int = Field(default=256, ge=1)
    # Day 4: the static export of the frontend, served at "/" (docs/day4-design.md section 3.7)
    ui_dir: str | None = None

    @property
    def provider(self) -> Provider:
        if self.use_mock_llm or self.llm_provider is None:
            return "mock"
        return self.llm_provider

    @property
    def base_url(self) -> str | None:
        """Chat Completions base URL of the ollama / openai providers."""
        if self.provider == "ollama":
            return self.llm_base_url or DEFAULT_OLLAMA_BASE_URL
        if self.provider == "openai":
            return self.llm_base_url
        return None

    @property
    def model_name(self) -> str:
        if self.provider == "ollama":
            return self.llm_model or DEFAULT_OLLAMA_MODEL
        if self.provider == "openai":
            return self.llm_model or "openai"
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
            "studio_enabled": self.studio_enabled,
            "studio_budgets_s": {
                "talking_point": self.studio_tp_budget_s,
                "commentary": self.studio_commentary_budget_s,
                "job": self.studio_job_budget_s,
                "recap": self.recap_budget_s,
            },
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

        def integer(name: str, default: int, low: int, high: int) -> int:
            value = get(name)
            if value is None:
                return default
            try:
                parsed = int(value)
            except ValueError:
                problems.append(f"{name} must be a whole number, got {value!r}")
                return default
            if not low <= parsed <= high:
                problems.append(f"{name} must be between {low} and {high}, got {value!r}")
                return default
            return parsed

        use_mock = flag("USE_MOCK_LLM", True)
        provider = (get("LLM_PROVIDER") or "").lower() or None
        if not use_mock:
            if provider in RETIRED:
                problems.append(RETIRED[provider])
            elif provider not in REAL_PROVIDERS:
                problems.append("USE_MOCK_LLM=false needs LLM_PROVIDER=ollama, openai or foundry")
            elif provider == "openai":
                for name in ("LLM_BASE_URL", "LLM_MODEL"):
                    if get(name) is None:
                        problems.append(f"LLM_PROVIDER=openai needs {name}")
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
        active = provider if not use_mock and provider in REAL_PROVIDERS else None
        timeout, budget = PROVIDER_TIMEOUTS.get(active or "", DEFAULT_TIMEOUTS)
        recap_budget = RECAP_BUDGETS.get(active or "mock", DEFAULT_RECAP_BUDGET_S)
        queue_size = integer("STUDIO_QUEUE_SIZE", 512, 8, 100_000)
        drop_at = integer("STUDIO_DROP_AT", 256, 1, 100_000)
        if drop_at > queue_size:
            problems.append("STUDIO_DROP_AT must not exceed STUDIO_QUEUE_SIZE")
        settings_values: dict[str, object] = {
            "use_mock_llm": use_mock,
            "llm_provider": provider if provider in REAL_PROVIDERS else None,
            "llm_base_url": get("LLM_BASE_URL"),
            "llm_model": get("LLM_MODEL"),
            "llm_api_key": SecretStr(key) if (key := get("LLM_API_KEY")) else None,
            "foundry_endpoint": get("FOUNDRY_ENDPOINT"),
            "foundry_model_deployment": get("FOUNDRY_MODEL_DEPLOYMENT"),
            "foundry_api_key": SecretStr(fkey) if (fkey := get("FOUNDRY_API_KEY")) else None,
            "llm_timeout_s": number("LLM_TIMEOUT_S", timeout),
            "claim_budget_s": number("CLAIM_BUDGET_S", budget),
            "mock_llm_faults": faults,
            "mock_llm_latency_ms": number("MOCK_LLM_LATENCY_MS", 0.0),
            "cors_origins": tuple(
                o.strip() for o in (get("CORS_ORIGINS") or DEFAULT_CORS).split(",") if o.strip()
            ),
            "studio_enabled": flag("STUDIO_ENABLED", True),
            "studio_llm_min_importance": integer("STUDIO_LLM_MIN_IMPORTANCE", 1, 1, 3),
            "studio_tp_budget_s": number("STUDIO_TP_BUDGET_S", 3.0),
            "studio_commentary_budget_s": number("STUDIO_COMMENTARY_BUDGET_S", 2.5),
            "studio_graphics_budget_s": number("STUDIO_GRAPHICS_BUDGET_S", 1.5),
            "studio_job_budget_s": number("STUDIO_JOB_BUDGET_S", 4.0),
            "recap_budget_s": number("RECAP_BUDGET_S", recap_budget),
            "studio_queue_size": queue_size,
            "studio_drop_at": min(drop_at, queue_size),
            "ui_dir": get("UI_DIR"),
        }
        if problems:
            raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(problems))
        return cls.model_validate(settings_values)


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (comments and blank lines ignored, quotes stripped).
    Windows editors add a BOM and PowerShell 5.1 `>` writes UTF-16: both are read."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    data = path.read_bytes()
    utf16 = data.startswith((b"\xff\xfe", b"\xfe\xff"))
    for line in data.decode("utf-16" if utf16 else "utf-8-sig").splitlines():
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
