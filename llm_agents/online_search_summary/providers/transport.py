"""llm_agents.online_search_summary.providers.transport — Retrying HTTP transport.

``RetryingJsonClient`` is the Bearer-auth JSON POST plumbing shared by the
providers. It retries ONLY genuinely transient failures — transport
errors, 429/5xx without an API error body, and the provider codes in
*retry_codes* — while balance/auth errors surface immediately: an API
error body must never burn retries on the status alone (ZhiPu signals
quota exhaustion as HTTP 429 + ``{"error": {"code": "1113", …}}``).

Also home of ``load_project_env`` — the project-root ``.env`` loader the
providers resolve their API keys through (setdefault only, so real
environment variables win).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import requests

from llm_agents.online_search_summary.core.errors import OnlineSearchError

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_project_env() -> None:
    """Load the project-root ``.env`` into os.environ (setdefault only)."""
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


class RetryingJsonClient:
    """Bearer-auth JSON POST session with transient-failure retries.

    *name* prefixes every log/warning line (the owning provider's name);
    *retry_statuses* / *retry_codes* / *backoff* carry the retry policy so
    a provider can tune it without subclassing the transport.
    """

    def __init__(self, name: str, api_key: str, *, max_attempts: int = 3,
                 retry_statuses: Sequence[int] = (429, 502, 503, 504),
                 retry_codes: Sequence[str] = ("1701", "1234"),
                 backoff: Sequence[float] = (1.0, 3.0)) -> None:
        self.name = name
        self.api_key = api_key
        self.max_attempts = max(1, max_attempts)
        self.retry_statuses = frozenset(retry_statuses)
        # Default codes: ZhiPu 1701 = concurrency limit, 1234 = upstream
        # "network error, retry later".
        self.retry_codes = frozenset(retry_codes)
        self.backoff = tuple(backoff)
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def post_json(self, url: str, payload: Dict[str, Any],
                  *, timeout: float) -> Dict[str, Any]:
        """Synchronous retrying JSON POST (run via asyncio.to_thread)."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[OnlineSearchError] = None
        for attempt in range(self.max_attempts):
            try:
                resp = self.session.post(url, headers=headers,
                                         json=payload, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                last_exc = OnlineSearchError(
                    f"{self.name}: transport error on {url}: "
                    f"{type(e).__name__}: {e}")
                logger.warning("    [%s] %s (attempt %d/%d)", self.name,
                               last_exc, attempt + 1, self.max_attempts)
                self._sleep_backoff(attempt)
                continue
            # Parse the body FIRST: ZhiPu signals quota/balance failures as
            # HTTP 429 + {"error": {"code": "1113", …}}, which must surface
            # immediately instead of burning retries on a status alone.
            try:
                data = resp.json()
            except ValueError:
                if resp.status_code in self.retry_statuses:
                    last_exc = OnlineSearchError(
                        f"{self.name}: HTTP {resp.status_code} on {url}",
                        status=resp.status_code)
                    logger.warning("    [%s] %s (attempt %d/%d)", self.name,
                                   last_exc, attempt + 1, self.max_attempts)
                    self._sleep_backoff(attempt)
                    continue
                raise OnlineSearchError(
                    f"{self.name}: non-JSON response (HTTP "
                    f"{resp.status_code}): {resp.text[:200]!r}",
                    status=resp.status_code) from None
            err = data.get("error") if isinstance(data, dict) else None
            if err is not None:
                code = str(err.get("code") or "")
                message = f"{self.name}: API error {code}: {err.get('message')}"
                if (code in self.retry_codes
                        and attempt < self.max_attempts - 1):
                    logger.warning("    [%s] %s (attempt %d/%d)", self.name,
                                   message, attempt + 1, self.max_attempts)
                    self._sleep_backoff(attempt)
                    continue
                raise OnlineSearchError(message, status=resp.status_code,
                                        code=code or None)
            if resp.status_code in self.retry_statuses:
                last_exc = OnlineSearchError(
                    f"{self.name}: HTTP {resp.status_code} on {url}",
                    status=resp.status_code)
                logger.warning("    [%s] %s (attempt %d/%d)", self.name,
                               last_exc, attempt + 1, self.max_attempts)
                self._sleep_backoff(attempt)
                continue
            if not resp.ok:
                raise OnlineSearchError(
                    f"{self.name}: HTTP {resp.status_code} on {url}: "
                    f"{resp.text[:200]!r}", status=resp.status_code)
            return data
        raise last_exc or OnlineSearchError(f"{self.name}: request failed")

    def _sleep_backoff(self, attempt: int) -> None:
        backoff = self.backoff[min(attempt, len(self.backoff) - 1)]
        time.sleep(backoff)

    async def post_json_async(self, url: str, payload: Dict[str, Any],
                              *, timeout: float) -> Dict[str, Any]:
        return await asyncio.to_thread(self.post_json, url, payload,
                                       timeout=timeout)
