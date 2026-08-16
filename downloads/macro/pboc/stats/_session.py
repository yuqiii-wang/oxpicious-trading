"""_session.py — curl_cffi-based anti-bot HTTP session for PBoC statistics pages.

The PBoC CDN (nginx + CDN cache) blocks ``requests``/urllib3 with HTTP 403
("Invalid Request") via TLS/JA3 fingerprinting — even with full browser
headers.  ``curl_cffi`` with ``impersonate="chrome"`` mimics Chrome's TLS
fingerprint and bypasses the block.

This module wraps ``curl_cffi.requests.Session`` with:
  * browser-profile rotation (chrome / chrome110 / chrome120 / edge)
  * LONG_SLEEP_INTERVAL (90 s) + jitter between requests
  * host-blocking detection (4xx → mark host, stop retrying)
  * optional Referer chain (visit parent before child)
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from curl_cffi import requests as cffi_requests
except ImportError as _e:  # pragma: no cover
    raise ImportError(
        "curl_cffi is required for PBoC stats downloads (TLS fingerprint bypass). "
        "Install with: pip install curl_cffi"
    ) from _e

from downloads._common.core import LONG_SLEEP_INTERVAL


PBOC_BASE = "https://www.pbc.gov.cn"

# curl_cffi impersonation targets — rotate to vary TLS fingerprint.
# All must be supported by the installed curl_cffi version.
_IMPERSONATE_PROFILES: List[str] = [
    "chrome",
    "chrome110",
    "chrome120",
    "chrome124",
    "edge101",
]


@dataclass
class CffiAntiBotConfig:
    """Configuration for the curl_cffi anti-bot session."""
    base_sleep_sec: float = LONG_SLEEP_INTERVAL
    sleep_jitter: float = 0.5  # 0..1, multiplied with base_sleep_sec
    enable_sleep: bool = True
    enable_host_tracking: bool = True
    rotate_profile: bool = True
    max_retries: int = 2
    retry_backoff_sec: float = 30.0
    timeout: int = 60


@dataclass
class HostBlockTracker:
    """Track hosts that return 4xx to avoid hammering blocked endpoints."""
    _blocked: Dict[str, float] = field(default_factory=dict)  # host -> block_time
    _block_duration: float = 3600.0  # 1 hour cooldown

    def record_error(self, url: str, status_code: int) -> None:
        host = urlparse(url).netloc
        self._blocked[host] = time.time()

    def is_blocked(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._blocked:
            return False
        if time.time() - self._blocked[host] > self._block_duration:
            del self._blocked[host]
            return False
        return True


class CffiAntiBotSession:
    """HTTP session using curl_cffi with anti-bot measures.

    Wraps ``curl_cffi.requests.Session`` to provide:
      * TLS fingerprint rotation (impersonate parameter)
      * Sleep with jitter between requests (LONG_SLEEP_INTERVAL default)
      * Host-blocking detection
      * Retry with backoff on transient failures

    Usage::

        session = CffiAntiBotSession(logger=my_logger)
        html = session.get_text(url)
        content = session.get_bytes(download_url)
    """

    def __init__(
        self,
        config: Optional[CffiAntiBotConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or CffiAntiBotConfig()
        self.logger = logger or logging.getLogger("pboc_stats")
        self._tracker = HostBlockTracker() if self.config.enable_host_tracking else None
        self._profile_idx = 0
        # curl_cffi Session persists cookies across requests
        self._session = cffi_requests.Session()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _next_profile(self) -> str:
        if not self.config.rotate_profile:
            return "chrome"
        profile = _IMPERSONATE_PROFILES[self._profile_idx % len(_IMPERSONATE_PROFILES)]
        self._profile_idx += 1
        return profile

    def _sleep(self, override: Optional[float] = None) -> None:
        if not self.config.enable_sleep:
            return
        base = override if override is not None else self.config.base_sleep_sec
        jitter = base * self.config.sleep_jitter * random.random()
        total = base + jitter
        time.sleep(total)

    def _is_blocked_response(self, status_code: int) -> bool:
        return 400 <= status_code < 500

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_blocked(self, url: str) -> bool:
        if self._tracker is None:
            return False
        return self._tracker.is_blocked(url)

    def get_text(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        sleep_sec: Optional[float] = None,
    ) -> Optional[str]:
        """GET request returning decoded text, or None on failure."""
        resp = self._request(url, referer=referer, sleep_sec=sleep_sec)
        if resp is None:
            return None
        # PBoC pages use UTF-8
        resp.encoding = "utf-8"
        return resp.text

    def get_bytes(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        sleep_sec: Optional[float] = None,
    ) -> Optional[bytes]:
        """GET request returning raw bytes (for xls/xlsx downloads)."""
        resp = self._request(url, referer=referer, sleep_sec=sleep_sec)
        if resp is None:
            return None
        return resp.content

    def _request(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        sleep_sec: Optional[float] = None,
    ) -> Optional[cffi_requests.Response]:
        """Perform a GET with retries, profile rotation, and anti-bot sleep."""
        if self.is_blocked(url):
            self.logger.warning("  [blocked] host already blocked, skipping: %s", url)
            return None

        headers: Dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"

        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            profile = self._next_profile()
            try:
                self.logger.debug(
                    "  [cffi] %s (impersonate=%s, attempt=%d)",
                    url, profile, attempt + 1,
                )
                resp = self._session.get(
                    url,
                    impersonate=profile,
                    headers=headers or None,
                    timeout=self.config.timeout,
                )
            except Exception as e:
                last_exc = e
                self.logger.warning(
                    "  [cffi] request failed (attempt %d): %s", attempt + 1, e,
                )
                if attempt < self.config.max_retries:
                    backoff = self.config.retry_backoff_sec * (attempt + 1)
                    self.logger.info("  [cffi] retrying in %.0fs ...", backoff)
                    time.sleep(backoff)
                continue

            if resp.status_code == 200:
                self._sleep(sleep_sec)
                return resp

            if self._is_blocked_response(resp.status_code):
                self.logger.error(
                    "  [cffi] HTTP %d for %s (impersonate=%s)",
                    resp.status_code, url, profile,
                )
                if self._tracker:
                    self._tracker.record_error(url, resp.status_code)
                # Retry with a different profile on 403
                if resp.status_code == 403 and attempt < self.config.max_retries:
                    backoff = self.config.retry_backoff_sec * (attempt + 1)
                    self.logger.info(
                        "  [cffi] 403 — retrying with different profile in %.0fs ...",
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                return None

            # 5xx or other — retry
            self.logger.warning(
                "  [cffi] HTTP %d for %s (attempt %d)",
                resp.status_code, url, attempt + 1,
            )
            if attempt < self.config.max_retries:
                time.sleep(self.config.retry_backoff_sec)
                continue
            return None

        if last_exc:
            self.logger.error("  [cffi] all retries exhausted: %s", last_exc)
        return None


def build_session(
    logger: Optional[logging.Logger] = None,
    *,
    sleep_sec: Optional[float] = None,
) -> CffiAntiBotSession:
    """Build a CffiAntiBotSession configured for PBoC stats downloads."""
    config = CffiAntiBotConfig(
        base_sleep_sec=sleep_sec if sleep_sec is not None else LONG_SLEEP_INTERVAL,
    )
    return CffiAntiBotSession(config=config, logger=logger)
