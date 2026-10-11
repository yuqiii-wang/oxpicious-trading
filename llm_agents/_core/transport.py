"""llm_agents._core.transport — Retrying HTTP transport.

``RetryingJsonClient`` is the Bearer-auth JSON POST plumbing shared by
every llm_agents provider. It retries ONLY genuinely transient failures —
transport errors, 429/5xx without an API error body, and the provider
codes in *retry_codes* — while balance/auth errors surface immediately:
an API error body must never burn retries on the status alone (ZhiPu
signals quota exhaustion as HTTP 429 + ``{"error": {"code": "1113", …}}``).

Every logical call is audited to ``monitor.external_resource_audit``
(``_common.external_resource_audit.record_llm`` — one LEAN row per call
with the FINAL attempt's outcome, fire-and-forget so auditing can never
perturb the request).

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
from typing import Any, Dict, Optional, Sequence, Tuple

import requests

from _common.external_resource_audit import record_llm
from llm_agents._core.errors import LlmProviderError

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        """Synchronous retrying JSON POST (run via asyncio.to_thread).

        Audits one ``monitor.external_resource_audit`` row per logical
        call — the FINAL attempt's outcome (retries are not itemized);
        duration_ms covers the whole logical call, retries + backoff
        included.
        """
        started = time.perf_counter()
        duration_ms: Optional[float] = None
        try:
            data, status_code = self._post_json_with_retries(
                url, payload, timeout=timeout)
            duration_ms = (time.perf_counter() - started) * 1000.0
        except LlmProviderError as e:
            record_llm(self.name, url, e.status, is_failed=True,
                       failure_reason=str(e),
                       duration_ms=(time.perf_counter() - started) * 1000.0)
            raise
        except Exception as e:  # noqa: BLE001 — audit, then re-raise as-is
            record_llm(self.name, url, None, is_failed=True,
                       failure_reason=f"{type(e).__name__}: {e}",
                       duration_ms=(time.perf_counter() - started) * 1000.0)
            raise
        record_llm(self.name, url, status_code, is_failed=False,
                   duration_ms=duration_ms)
        return data

    def _post_json_with_retries(self, url: str, payload: Dict[str, Any],
                                *, timeout: float) -> Tuple[Dict[str, Any], int]:
        """The retry loop — returns (parsed JSON, final HTTP status)."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[LlmProviderError] = None
        for attempt in range(self.max_attempts):
            try:
                resp = self.session.post(url, headers=headers,
                                         json=payload, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                last_exc = LlmProviderError(
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
                    last_exc = LlmProviderError(
                        f"{self.name}: HTTP {resp.status_code} on {url}",
                        status=resp.status_code)
                    logger.warning("    [%s] %s (attempt %d/%d)", self.name,
                                   last_exc, attempt + 1, self.max_attempts)
                    self._sleep_backoff(attempt)
                    continue
                raise LlmProviderError(
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
                raise LlmProviderError(message, status=resp.status_code,
                                       code=code or None)
            if resp.status_code in self.retry_statuses:
                last_exc = LlmProviderError(
                    f"{self.name}: HTTP {resp.status_code} on {url}",
                    status=resp.status_code)
                logger.warning("    [%s] %s (attempt %d/%d)", self.name,
                               last_exc, attempt + 1, self.max_attempts)
                self._sleep_backoff(attempt)
                continue
            if not resp.ok:
                raise LlmProviderError(
                    f"{self.name}: HTTP {resp.status_code} on {url}: "
                    f"{resp.text[:200]!r}", status=resp.status_code)
            return data, resp.status_code
        raise last_exc or LlmProviderError(f"{self.name}: request failed")

    def _sleep_backoff(self, attempt: int) -> None:
        backoff = self.backoff[min(attempt, len(self.backoff) - 1)]
        time.sleep(backoff)

    async def post_json_async(self, url: str, payload: Dict[str, Any],
                              *, timeout: float) -> Dict[str, Any]:
        return await asyncio.to_thread(self.post_json, url, payload,
                                       timeout=timeout)
