"""llm_agents._core.base — Shared provider-client base.

``BaseLlmProvider`` is the plumbing every llm_agents provider base
(``BaseOnlineSearchProvider``, ``BaseLlmAskProvider``) inherits, so the
API-key resolution, retry policy, and JSON-POST helpers live exactly
once:

  * API-key resolution — the explicit env chain first
    (``API_KEY_ENV_VARS`` declares the provider's env vars, first match
    wins — see ``transport.load_project_env``), then the DB secret
    registry ``static.secret_config`` keyed by ``name`` +
    ``SECRET_TYPE`` (the root .env's successor,
    ``_core.secret_config``);
  * the retry policy handed to ``RetryingJsonClient`` (provider codes —
    ZhiPu 1701 = concurrency limit, 1234 = upstream "network error,
    retry later");
  * ``_post_json`` / ``_post_json_async`` and the ``session`` property.

Subclass bases add their domain contract on top (search/summarize or
plain ask); concrete providers set ``name`` / ``API_KEY_ENV_VARS`` /
their defaults and implement the abstract methods of their base.
"""
from __future__ import annotations

import abc
import os
from typing import Any, ClassVar, Dict, Optional, Sequence

import requests

from llm_agents._core.errors import LlmProviderError
from llm_agents._core.transport import RetryingJsonClient, load_project_env


class BaseLlmProvider(abc.ABC):
    """Shared client plumbing for every llm_agents provider.

    Owns API-key resolution, the retrying JSON transport, and the POST
    helpers; declares no abstract methods itself — the per-agent bases
    subclass this and add their contracts.
    """

    name: ClassVar[str] = "<base>"
    API_KEY_ENV_VARS: ClassVar[Sequence[str]] = ()
    # WHICH CREDENTIAL the provider resolves from the DB secret
    # registry (static.secret_config — the root .env's successor): the
    # audit's selected-domain contract, llm -> ext_llm_provider,
    # web_search -> ext_http_source. Search providers override to
    # "web_search"; chat providers keep the default.
    SECRET_TYPE: ClassVar[str] = "llm"

    # Retry policy handed to the transport (see RetryingJsonClient):
    # provider codes — ZhiPu 1701 = concurrency limit, 1234 = upstream
    # "network error, retry later".
    _RETRY_STATUSES = frozenset({429, 502, 503, 504})
    _RETRY_CODES = frozenset({"1701", "1234"})
    _RETRY_BACKOFF_SEC = (1.0, 3.0)

    def __init__(self, *, api_key: Optional[str] = None,
                 max_attempts: int = 3) -> None:
        self.api_key = api_key or self._resolve_api_key()
        self.max_attempts = max(1, max_attempts)
        self._http = RetryingJsonClient(
            self.name, self.api_key, max_attempts=self.max_attempts,
            retry_statuses=self._RETRY_STATUSES,
            retry_codes=self._RETRY_CODES,
            backoff=self._RETRY_BACKOFF_SEC)

    # -- key / transport -------------------------------------------------
    @classmethod
    def _resolve_api_key(cls) -> str:
        # 1. explicit env (the externally-set chain, first match wins);
        # 2. the DB secret registry (static.secret_config — the root
        #    .env's keys migrated there 2026-11-06 and the file was
        #    deleted; the newest active row for cls.name + cls.SECRET_TYPE
        #    wins, TTL-cached fail-soft in _core.secret_config).
        load_project_env()
        for var in cls.API_KEY_ENV_VARS:
            key = os.environ.get(var, "").strip()
            if key:
                return key
        from llm_agents._core.secret_config import get_secret

        key = get_secret(cls.name, cls.SECRET_TYPE)
        if key:
            return key
        raise LlmProviderError(
            f"{cls.name}: no API key — set one of {list(cls.API_KEY_ENV_VARS)} "
            f"in the environment, or store the credential on the "
            f"{cls.SECRET_TYPE} / {cls.name} row of static.secret_config "
            f"(Config → Secrets tab).")

    @property
    def session(self) -> requests.Session:
        return self._http.session

    def _post_json(self, url: str, payload: Dict[str, Any],
                   *, timeout: float,
                   budget: Optional[float] = None) -> Dict[str, Any]:
        return self._http.post_json(url, payload, timeout=timeout,
                                    budget=budget)

    async def _post_json_async(self, url: str, payload: Dict[str, Any],
                               *, timeout: float,
                               budget: Optional[float] = None) -> Dict[str, Any]:
        return await self._http.post_json_async(url, payload, timeout=timeout,
                                                budget=budget)
