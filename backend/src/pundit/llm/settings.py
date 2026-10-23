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
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pundit.agents.contracts import Lang
from pundit.agents.voices import SPOKEN_LANGUAGES
from pundit.domain.errors import PunditError
from pundit.llm.faults import FaultPlan, TtsFaultPlan

# Day 5 voice (docs/day5-design.md section 3.7)
TtsProvider = Literal["off", "browser", "mock", "azure"]
AudioStoreKind = Literal["none", "local", "blob"]
SttProvider = Literal["off", "mock", "azure"]
DEFAULT_AUDIO_STORE_DIR = Path(".data") / "audio"  # relative to the working directory (backend/)
DEFAULT_AZURE_RPM = 18
# Day 6 MCP (docs/day6-design.md section 2.4)
DEFAULT_MCP_HOSTS = ("127.0.0.1", "localhost")
DEFAULT_MCP_RATE = 60

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
ZERO_ALLOWED = frozenset({"MOCK_LLM_LATENCY_MS", "MOCK_TTS_LATENCY_MS"})
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
    # Day 6: how long an open LLM breaker (claims, studio, recaps) waits before a trial call;
    # shortened only by the fault-injection runs (docs/day6-design.md 5.7)
    llm_breaker_cooldown_s: float = Field(default=60.0, gt=0)
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
    # Day 5: voice (docs/day5-design.md section 3.7)
    tts_provider: TtsProvider = "mock"
    tts_languages: tuple[Lang, ...] = ("vi",)
    speech_region: str | None = None
    speech_key: SecretStr | None = None  # dev only; managed identity on Azure
    speech_endpoint: str | None = None  # custom domain, needed by Entra ID
    speech_resource_id: str | None = None
    tts_max_rpm: int | None = Field(default=None, ge=1, le=100_000)  # None: by provider
    tts_max_concurrency: int = Field(default=4, ge=1, le=64)
    tts_timeout_s: float = Field(default=4.0, gt=0)
    tts_recap_timeout_s: float = Field(default=45.0, gt=0)
    tts_get_wait_s: float = Field(default=10.0, gt=0)
    tts_max_speed: float = Field(default=20.0, gt=0)
    tts_daily_char_budget: int = Field(default=200_000, ge=0)
    audio_cache_dir: str | None = None
    audio_cache_max_mb: int = Field(default=256, ge=1)
    audio_store: AudioStoreKind = "local"
    audio_store_dir: str | None = None
    audio_blob_account_url: str | None = None
    audio_blob_container: str = "audio"
    audio_blob_connection_string: SecretStr | None = None  # Azurite / dev only
    mock_tts_latency_ms: float = Field(default=0.0, ge=0)
    mock_tts_faults: str = ""
    mock_tts_time_scale: float = Field(default=1.0, gt=0, le=10)
    stt_provider: SttProvider = "off"
    stt_max_seconds: float = Field(default=15.0, gt=0, le=60)
    mock_stt_text: str | None = None
    # Day 6: MCP on /mcp (docs/day6-design.md section 2.4)
    mcp_enabled: bool = True
    mcp_allowed_hosts: tuple[str, ...] = DEFAULT_MCP_HOSTS
    mcp_rate_per_minute: int = Field(default=DEFAULT_MCP_RATE, ge=1, le=100_000)
    trust_proxy: bool = False  # X-Forwarded-For names the client (Azure ingress)
    # Day 6: sessions (docs/day6-design.md section 6.4)
    max_sessions: int = Field(default=16, ge=1, le=256)
    private_idle_s: float = Field(default=900.0, gt=0)  # unwatched private session: removed
    shared_idle_s: float = Field(default=3600.0, gt=0)  # unwatched shared session: removed
    private_per_ip: int = Field(default=3, ge=1, le=100)  # new private sessions per 10 minutes

    @property
    def speech_enabled(self) -> bool:
        return self.tts_provider != "off"

    @property
    def rate_per_minute(self) -> int:
        """Syntheses per minute: TTS_MAX_RPM, else 18 for Azure (the Free tier allows 20 per
        60 s) and no real limit for the mock (tests and the demo without Azure)."""
        if self.tts_max_rpm is not None:
            return self.tts_max_rpm
        return DEFAULT_AZURE_RPM if self.tts_provider == "azure" else 60_000

    @property
    def cache_dir(self) -> Path:
        return Path(self.audio_cache_dir or Path(tempfile.gettempdir()) / "pundit-audio")

    @property
    def store_dir(self) -> Path:
        return Path(self.audio_store_dir) if self.audio_store_dir else DEFAULT_AUDIO_STORE_DIR

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
            "llm_breaker_cooldown_s": self.llm_breaker_cooldown_s,
            "studio_enabled": self.studio_enabled,
            "studio_budgets_s": {
                "talking_point": self.studio_tp_budget_s,
                "commentary": self.studio_commentary_budget_s,
                "job": self.studio_job_budget_s,
                "recap": self.recap_budget_s,
            },
            "speech": {
                "tts_provider": self.tts_provider,
                "tts_languages": list(self.tts_languages),
                "audio_store": self.audio_store,
                "stt_provider": self.stt_provider,
                "mock_tts_faults": self.mock_tts_faults or None,
            },
            "mcp": {
                "enabled": self.mcp_enabled,
                "allowed_hosts": list(self.mcp_allowed_hosts),
                "rate_per_minute": self.mcp_rate_per_minute,
            },
            "trust_proxy": self.trust_proxy,
            "sessions": {
                "max": self.max_sessions,
                "private_idle_s": self.private_idle_s,
                "shared_idle_s": self.shared_idle_s,
                "private_per_ip": self.private_per_ip,
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
            if parsed < 0 or (parsed == 0 and name not in ZERO_ALLOWED):
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
        speech = _speech_values(get, number, integer, problems)
        hosts = _hosts(get("MCP_ALLOWED_HOSTS"), problems)
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
            "llm_breaker_cooldown_s": number("LLM_BREAKER_COOLDOWN_S", 60.0),
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
            **speech,
            "mcp_enabled": flag("MCP_ENABLED", True),
            "mcp_allowed_hosts": hosts,
            "mcp_rate_per_minute": integer("MCP_RATE_PER_MINUTE", DEFAULT_MCP_RATE, 1, 100_000),
            "trust_proxy": flag("TRUST_PROXY", False),
            "max_sessions": integer("MAX_SESSIONS", 16, 1, 256),
            "private_idle_s": number("PRIVATE_IDLE_S", 900.0),
            "shared_idle_s": number("SHARED_IDLE_S", 3600.0),
            "private_per_ip": integer("PRIVATE_PER_IP", 3, 1, 100),
        }
        if problems:
            raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(problems))
        return cls.model_validate(settings_values)


def _hosts(value: str | None, problems: list[str]) -> tuple[str, ...]:
    """MCP_ALLOWED_HOSTS: host names, comma separated ("127.0.0.1,localhost,app.example")."""
    if value is None:
        return DEFAULT_MCP_HOSTS
    hosts: list[str] = []
    for raw in value.split(","):
        host = raw.strip().lower()
        if not host:
            continue
        if "/" in host or " " in host or ":" in host.removeprefix("[").split("]")[-1]:
            problems.append(
                f"MCP_ALLOWED_HOSTS lists host names without scheme, port or path, got {raw!r}"
            )
        elif host not in hosts:
            hosts.append(host)
    return tuple(hosts) or DEFAULT_MCP_HOSTS


def _choice[T: str](value: str | None, choices: tuple[T, ...], default: T) -> T | None:
    """`value` as one of `choices` (case-insensitive), `default` when missing, None when bad."""
    if value is None:
        return default
    for choice in choices:
        if choice == value.lower():
            return choice
    return None


def _speech_values(
    get: Callable[[str], str | None],
    number: Callable[[str, float], float],
    integer: Callable[[str, int, int, int], int],
    problems: list[str],
) -> dict[str, object]:
    """The Day 5 variables (docs/day5-design.md section 3.7), checked like the others."""
    tts = _choice(get("TTS_PROVIDER"), get_args(TtsProvider), "mock")
    if tts is None:
        problems.append("TTS_PROVIDER must be off, browser, mock or azure")
        tts = "mock"
    stt = _choice(get("STT_PROVIDER"), get_args(SttProvider), "off")
    if stt is None:
        problems.append("STT_PROVIDER must be off, mock or azure")
        stt = "off"
    store = _choice(get("AUDIO_STORE"), get_args(AudioStoreKind), "local")
    if store is None:
        problems.append("AUDIO_STORE must be none, local or blob")
        store = "local"
    languages: list[str] = []
    for code in (get("TTS_LANGUAGES") or "vi").split(","):
        code = code.strip().lower()
        if not code:
            continue
        if code not in SPOKEN_LANGUAGES:
            problems.append(f"TTS_LANGUAGES: {code!r} has no spoken pack (supported: vi)")
        elif code not in languages:
            languages.append(code)
    if not languages:
        languages = ["vi"]
    key, region = get("SPEECH_KEY"), get("SPEECH_REGION")
    endpoint, resource = get("SPEECH_ENDPOINT"), get("SPEECH_RESOURCE_ID")
    if "azure" in (tts, stt) and not ((key and region) or (endpoint and resource)):
        problems.append(
            "Azure Speech needs SPEECH_KEY + SPEECH_REGION (dev) or SPEECH_ENDPOINT + "
            "SPEECH_RESOURCE_ID (Entra ID / managed identity)"
        )
    blob_url, blob_conn = get("AUDIO_BLOB_ACCOUNT_URL"), get("AUDIO_BLOB_CONNECTION_STRING")
    if store == "blob" and not (blob_url or blob_conn):
        problems.append("AUDIO_STORE=blob needs AUDIO_BLOB_ACCOUNT_URL (or a connection string)")
    faults = get("MOCK_TTS_FAULTS") or ""
    if faults:
        try:
            TtsFaultPlan.parse(faults)
        except ValueError as exc:
            problems.append(f"MOCK_TTS_FAULTS: {exc}")
    return {
        "tts_provider": tts,
        "tts_languages": tuple(languages),
        "speech_region": region,
        "speech_key": SecretStr(key) if key else None,
        "speech_endpoint": endpoint.rstrip("/") if endpoint else None,
        "speech_resource_id": resource,
        "tts_max_rpm": integer("TTS_MAX_RPM", 0, 1, 100_000) or None,
        "tts_max_concurrency": integer("TTS_MAX_CONCURRENCY", 4, 1, 64),
        "tts_timeout_s": number("TTS_TIMEOUT_S", 4.0),
        "tts_recap_timeout_s": number("TTS_RECAP_TIMEOUT_S", 45.0),
        "tts_get_wait_s": number("TTS_GET_WAIT_S", 10.0),
        "tts_max_speed": number("TTS_MAX_SPEED", 20.0),
        "tts_daily_char_budget": integer("TTS_DAILY_CHAR_BUDGET", 200_000, 0, 1_000_000_000),
        "audio_cache_dir": get("AUDIO_CACHE_DIR"),
        "audio_cache_max_mb": integer("AUDIO_CACHE_MAX_MB", 256, 1, 100_000),
        "audio_store": store,
        "audio_store_dir": get("AUDIO_STORE_DIR"),
        "audio_blob_account_url": blob_url,
        "audio_blob_container": get("AUDIO_BLOB_CONTAINER") or "audio",
        "audio_blob_connection_string": SecretStr(blob_conn) if blob_conn else None,
        "mock_tts_latency_ms": number("MOCK_TTS_LATENCY_MS", 0.0),
        "mock_tts_faults": faults,
        "mock_tts_time_scale": min(number("MOCK_TTS_TIME_SCALE", 1.0), 10.0),
        "stt_provider": stt,
        "stt_max_seconds": min(number("STT_MAX_SECONDS", 15.0), 60.0),
        "mock_stt_text": get("MOCK_STT_TEXT"),
    }


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
