"""Credentials of Azure Speech (docs/day5-design.md section 8.4).

Key (dev): `Ocp-Apim-Subscription-Key`. Microsoft Entra ID (managed identity on Azure, `az login`
locally): `Authorization: Bearer aad#{resource id}#{token}` on the resource's custom domain, with
the Cognitive Services Speech User role. The token is cached until five minutes before it expires
and fetched by one caller at a time. Secrets never appear in `repr` or logs.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import SecretStr

from pundit.llm.settings import Settings

SPEECH_SCOPE = "https://cognitiveservices.azure.com/.default"
REFRESH_BEFORE_S = 300.0


class SpeechAuth(Protocol):
    @property
    def mode(self) -> Literal["key", "entra"]: ...

    async def headers(self) -> dict[str, str]: ...

    def invalidate(self) -> None: ...


class KeyAuth:
    def __init__(self, key: SecretStr) -> None:
        self._key = key

    @property
    def mode(self) -> Literal["key"]:
        return "key"

    async def headers(self) -> dict[str, str]:
        return {"Ocp-Apim-Subscription-Key": self._key.get_secret_value()}

    def invalidate(self) -> None:
        return None

    def __repr__(self) -> str:
        return "KeyAuth(***)"


def _default_credential() -> Any:
    from azure.identity import DefaultAzureCredential  # lazy: dev and CI never need it

    return DefaultAzureCredential()


class EntraAuth:
    def __init__(
        self,
        resource_id: str,
        *,
        credential_factory: Callable[[], Any] = _default_credential,
        wall_clock: Callable[[], float] = time.time,  # noqa: TID251 - token expiry is wall time
    ) -> None:
        self.resource_id = resource_id
        self._factory = credential_factory
        self._credential: Any = None
        self._wall = wall_clock
        self._token: str | None = None
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> Literal["entra"]:
        return "entra"

    async def _fresh_token(self) -> str:
        async with self._lock:
            if self._token is not None and self._wall() < self._refresh_at:
                return self._token
            if self._credential is None:
                self._credential = self._factory()
            access = await asyncio.to_thread(self._credential.get_token, SPEECH_SCOPE)
            self._token = str(access.token)
            self._refresh_at = float(access.expires_on) - REFRESH_BEFORE_S
            return self._token

    async def headers(self) -> dict[str, str]:
        token = await self._fresh_token()
        return {"Authorization": f"Bearer aad#{self.resource_id}#{token}"}

    def invalidate(self) -> None:
        self._token = None

    def __repr__(self) -> str:
        return f"EntraAuth(resource={self.resource_id!r})"


def build_auth(settings: Settings) -> SpeechAuth | None:
    if settings.speech_endpoint and settings.speech_resource_id:
        return EntraAuth(settings.speech_resource_id)
    if settings.speech_key is not None:
        return KeyAuth(settings.speech_key)
    return None
