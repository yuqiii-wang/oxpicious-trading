"""HTTP/network layer for all downloaders.

Constants (timeouts, sleep cadences, headers, browser profiles), logger
setup, session builders, host-blocking tracking and the unified
AntiBotProxy behind every external HTTP request — exchange downloaders,
streamers, macro/news fetchers, and the API-key services (zhihu search).
Every request outcome through AntiBotProxy is audited to
monitor.external_resource_audit (_common/external_resource_audit.py —
fire-and-forget, never blocks).

The proxy is session-agnostic: any object exposing ``get``/``post`` with
requests-like kwargs works, including ``curl_cffi.requests.Session``
(TLS-fingerprint sessions — pass per-request session kwargs such as
``impersonate=...`` or ``stream=True`` through ``request_kwargs``).
API-key auth is first-class: ``api_key`` + ``api_key_header`` +
``api_key_scheme`` inject the credential header at request time without
touching the caller's headers dict.

``llm_agents._core.transport.RetryingJsonClient`` (the LLM providers'
transport) intentionally does NOT route through the proxy: LLM APIs
signal quota/auth errors in 4xx BODIES (ZhiPu 429 + error code), which
the proxy's suppress-on-4xx contract would discard, and it audits each
logical call's FINAL outcome itself via ``record_llm``.

Pacing values (sleep/jitter/timeouts/anti-bot toggles/blocked-host
cooldown) resolve at RUNTIME from the static external_request_config
JSON tree (database/sql/monitoring/static/external_request_config/ —
_common/request_config.py) whenever a proxy is built with
``AntiBotProxy(service=...)``; a proxy built with an explicit
``AntiBotConfig`` keeps every value the caller passed. The DEFAULT_*
constants below are the last-resort fallback for services with neither
their own JSON file nor a usable default.json row — and stay exported
for backward compatibility.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

from _common.external_resource_audit import record_http
from _common.request_config import (
    FALLBACK_SLEEP_JITTER,
    FALLBACK_SLEEP_SEC,
    FALLBACK_TIMEOUT_CONNECT_SEC,
    FALLBACK_TIMEOUT_READ_SEC,
    host_block_cooldown_for,
    request_config_for,
)


DEFAULT_TIMEOUT: Tuple[Optional[float], Optional[float]] = (
    FALLBACK_TIMEOUT_CONNECT_SEC, FALLBACK_TIMEOUT_READ_SEC,
)

# Shared default sleep seconds between HTTP requests for anti-bot protection.
# Fallback only — the runtime source is the external_request_config JSON tree
# (see module docstring); individual downloaders resolve their per-service
# cadence from it via sleep_sec_for / AntiBotProxy(service=...).
DEFAULT_SLEEP_SEC = FALLBACK_SLEEP_SEC
DEFAULT_SHORT_SLEEP_SEC = 8.0
# Long sleep for aggressive anti-bot sites (e.g. cninfo, SSE dividend endpoint
# when called at quarterly cadence). 90s between requests makes a full ETF-held
# sweep take ~hours but is the safest cadence for sites that block on volume.
LONG_SLEEP_INTERVAL = 90.0
VERY_LONG_SLEEP_INTERVAL = 300.0
SUPER_LONG_SLEEP_INTERVAL = 600.0

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)
DEFAULT_ACCEPT_LANG = "zh-CN,zh;q=0.9,en;q=0.8"

COMMON_BASE_HEADERS: Dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": DEFAULT_ACCEPT,
    "Accept-Language": DEFAULT_ACCEPT_LANG,
    "Connection": "keep-alive",
}

BROWSER_PROFILES: List[Dict[str, str]] = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"125\", \"Google Chrome\";v=\"125\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"macOS\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Linux\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Edge/126.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Microsoft Edge\";v=\"126\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
            "Gecko/20100101 Firefox/127.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) "
            "Gecko/20100101 Firefox/127.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) "
            "Gecko/20100101 Firefox/127.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Edge/125.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"125\", \"Microsoft Edge\";v=\"125\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
]


def random_browser_profile() -> Dict[str, str]:
    return dict(random.choice(BROWSER_PROFILES))


_BROWSER_FINGERPRINT_KEYS = {
    "User-Agent",
    "Sec-Ch-Ua",
    "Sec-Ch-Ua-Mobile",
    "Sec-Ch-Ua-Platform",
    "Sec-Ch-Ua-Platform-Version",
    "Sec-Fetch-Site",
    "Sec-Fetch-Mode",
    "Sec-Fetch-Dest",
    "Sec-Fetch-User",
    "Accept",
    "Accept-Language",
}


def merge_browser_profile(base_headers: Dict[str, str]) -> Dict[str, str]:
    """Overlay random browser fingerprint fields onto base headers.

    Only browser-specific keys (User-Agent, Sec-Ch-Ua-*, Sec-Fetch-*, Accept,
    Accept-Language) are overlaid, preventing site-specific headers like
    Content-Type or Referer from being accidentally overwritten.
    """
    result = dict(base_headers)
    profile = random_browser_profile()
    for key in _BROWSER_FINGERPRINT_KEYS:
        if key in profile:
            result[key] = profile[key]
    return result


def random_sleep(base_sec: float, jitter_factor: float = 0.5) -> None:
    """Sleep for a random duration around base_sec with jitter."""
    if base_sec <= 0:
        return
    jitter = base_sec * jitter_factor
    sleep_time = random.uniform(base_sec - jitter, base_sec + jitter)
    time.sleep(max(0, sleep_time))


def random_sleep_range(min_sec: float, max_sec: float) -> None:
    """Sleep for a random duration between min_sec and max_sec."""
    if min_sec <= 0 and max_sec <= 0:
        return
    sleep_time = random.uniform(min(min_sec, max_sec), max(min_sec, max_sec))
    time.sleep(max(0, sleep_time))


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    # Delegates to the central setup so every downloader gets console +
    # logs/<YYYY-MM-DD>/<name>.log file output; the first caller names the file.
    from _common.log_setup import setup_logging

    return setup_logging(name, level=level)


def build_headers_with_referer(
    referer: str, extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    h = dict(COMMON_BASE_HEADERS)
    h["Referer"] = referer
    if extra:
        h.update(extra)
    return h


def build_default_session(headers: Optional[Dict[str, str]] = None) -> requests.Session:
    s = requests.Session()
    base = dict(COMMON_BASE_HEADERS)
    if headers:
        base.update(headers)
    s.headers.update(base)
    return s


# ---------------------------------------------------------------------------
# Host blocking detection: track 4xx errors per host and skip subsequent requests
# ---------------------------------------------------------------------------

@dataclass
class HostStatus:
    blocked: bool = False
    blocked_reason: str = ""
    last_error_time: float = 0.0
    error_count: int = 0


class HostStatusTracker:
    # Default auto-unblock delay for a 4xx-blocked host, so a transient
    # rate-limit (e.g. OSS 403 mid-sweep) doesn't poison the whole process
    # run.  If the retry also 4xx's, record_error re-blocks with a fresh
    # cooldown timer. Instances take a per-service override from the
    # external_request_config JSON (host_block_cooldown_sec) via the
    # block_cooldown_sec constructor arg.
    BLOCK_COOLDOWN_SEC: float = 600.0
    # HTTP codes that mean genuine anti-bot blocking / rate limiting.  A 404
    # is a legitimate per-resource miss (e.g. an index with no closeweight.xls
    # on the OSS bucket) and must NOT block the whole host.
    BLOCKING_STATUS_CODES: frozenset = frozenset({403, 429})

    def __init__(self, block_cooldown_sec: Optional[float] = None):
        self._host_status: Dict[str, HostStatus] = {}
        self.block_cooldown_sec = (
            self.BLOCK_COOLDOWN_SEC if block_cooldown_sec is None
            else float(block_cooldown_sec)
        )

    def is_blocked(self, url: str) -> bool:
        host = self._extract_host(url)
        status = self._host_status.get(host)
        if status is None or not status.blocked:
            return False
        if time.time() - status.last_error_time >= self.block_cooldown_sec:
            status.blocked = False
            status.blocked_reason = ""
            logger = setup_logger("host_tracker")
            logger.warning(
                "Host %s auto-unblocked after %.0fs cooldown, resuming requests",
                host, self.block_cooldown_sec,
            )
            return False
        return True

    def record_error(self, url: str, status_code: int, reason: str = "") -> None:
        host = self._extract_host(url)
        status = self._host_status.setdefault(host, HostStatus())
        status.error_count += 1
        status.last_error_time = time.time()
        if status_code in self.BLOCKING_STATUS_CODES:
            status.blocked = True
            status.blocked_reason = reason or f"HTTP {status_code}"
            logger = setup_logger("host_tracker")
            logger.warning("Host %s blocked due to %s", host, status.blocked_reason)

    def unblock(self, url: str) -> None:
        host = self._extract_host(url)
        if host in self._host_status:
            self._host_status[host].blocked = False
            self._host_status[host].blocked_reason = ""

    def get_status(self, url: str) -> Optional[HostStatus]:
        host = self._extract_host(url)
        return self._host_status.get(host)

    @staticmethod
    def _extract_host(url: str) -> str:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc or parsed.hostname or url
        except Exception:
            return url


# ---------------------------------------------------------------------------
# Unified Anti-Bot Proxy: consolidates all anti-bot mechanisms into one class
# ---------------------------------------------------------------------------

@dataclass
class AntiBotConfig:
    """Configuration for anti-bot behavior.

    All anti-bot features are enabled by default and can be selectively disabled.
    """
    # Browser fingerprint rotation
    rotate_browser_profile: bool = True
    # Add random parameter to requests
    add_random_param: bool = True
    # Sleep between requests
    enable_sleep: bool = True
    # Base sleep duration (seconds)
    base_sleep_sec: float = DEFAULT_SLEEP_SEC
    # Jitter factor for sleep (0.0 = no jitter, 1.0 = 100% jitter)
    sleep_jitter: float = FALLBACK_SLEEP_JITTER
    # Track host blocking (4xx errors)
    enable_host_tracking: bool = True
    # Timeout for requests
    timeout: Tuple[Optional[float], Optional[float]] = DEFAULT_TIMEOUT


def antibot_config_for(
    service: str, request_type: str = "default"
) -> AntiBotConfig:
    """Build an AntiBotConfig from the service's external_request_config
    JSON row (sleep/jitter/timeouts/anti-bot toggles).

    For proxies that take the row as-is, prefer ``AntiBotProxy(service=...)``.
    Use this when a call site must override one field (e.g. a computed base
    sleep) while the rest of the row still applies — construct
    ``dataclasses.replace(antibot_config_for(SERVICE), base_sleep_sec=x)``.
    """
    cfg = request_config_for(service, request_type)
    return AntiBotConfig(
        rotate_browser_profile=cfg.rotate_browser_profile,
        add_random_param=cfg.add_random_param,
        enable_host_tracking=cfg.enable_host_tracking,
        base_sleep_sec=cfg.sleep_sec,
        sleep_jitter=cfg.sleep_jitter,
        timeout=(cfg.timeout_connect_sec, cfg.timeout_read_sec),
    )


class AntiBotProxy:
    """Unified anti-bot proxy that consolidates browser fingerprint rotation,
    sleep with jitter, host blocking detection, and request parameter randomization.

    This class provides a single interface for all anti-bot mechanisms, making
    it easy to configure and use across the entire codebase.

    Build proxies with ``service=`` so the pacing values (base sleep,
    jitter, timeouts, anti-bot toggles, blocked-host cooldown) resolve
    from the service's external_request_config JSON row at construction
    time. An explicit ``config`` still wins field-for-field (computed
    overrides such as an adaptive base sleep keep working) — in that case
    consider ``antibot_config_for(service)`` + ``dataclasses.replace`` so
    the untouched fields still come from the JSON.

    Example usage:
        proxy = AntiBotProxy(service="downloads.etf.sse.composition")
        session = requests.Session()

        # Simple GET with anti-bot protection
        resp = proxy.get(session, url, headers=base_headers)

        # POST with custom sleep (overrides the service row for this call)
        resp = proxy.post(session, url, data=payload, sleep_sec=30.0)

        # Manual sleep after processing
        proxy.sleep()

        # Check if host is blocked
        if proxy.is_blocked(url):
            # Handle blocked host
            pass
    """

    def __init__(self, config: Optional[AntiBotConfig] = None,
                 audit_service: Optional[str] = None,
                 service: Optional[str] = None,
                 request_type: str = "default"):
        # service= resolves the pacing row from the static JSON tree;
        # an explicitly passed config always wins over it.
        if config is None and service is not None:
            config = antibot_config_for(service, request_type)
        self.config = config or AntiBotConfig()
        self._host_tracker = None
        if self.config.enable_host_tracking:
            cooldown = (
                host_block_cooldown_for(service, request_type)
                if service is not None else None
            )
            self._host_tracker = HostStatusTracker(block_cooldown_sec=cooldown)
        # Service name stamped on this proxy's external_resource_audit rows.
        # None = the process-resolved name (service_heartbeat registration,
        # __main__ fallback) — pass it explicitly when the owning pipeline
        # is known and the process may outlive its code (long-lived runners).
        self.audit_service = audit_service

    def get(
        self,
        session: requests.Session,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[Tuple[int, int]] = None,
        sleep_sec: Optional[float] = None,
        anti_bot: bool = True,
        api_key: Optional[str] = None,
        api_key_header: str = "Authorization",
        api_key_scheme: str = "Bearer",
        request_kwargs: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Perform a GET request with anti-bot protection."""
        return self._request(
            session, "get", url,
            params=params, headers=headers, data=None,
            timeout=timeout, sleep_sec=sleep_sec,
            anti_bot=anti_bot,
            api_key=api_key, api_key_header=api_key_header,
            api_key_scheme=api_key_scheme,
            request_kwargs=request_kwargs,
            logger=logger, log_tag=log_tag,
        )

    def post(
        self,
        session: requests.Session,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[Tuple[int, int]] = None,
        sleep_sec: Optional[float] = None,
        anti_bot: bool = True,
        api_key: Optional[str] = None,
        api_key_header: str = "Authorization",
        api_key_scheme: str = "Bearer",
        request_kwargs: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Perform a POST request with anti-bot protection."""
        return self._request(
            session, "post", url,
            params=params, headers=headers, data=data,
            timeout=timeout, sleep_sec=sleep_sec,
            anti_bot=anti_bot,
            api_key=api_key, api_key_header=api_key_header,
            api_key_scheme=api_key_scheme,
            request_kwargs=request_kwargs,
            logger=logger, log_tag=log_tag,
        )

    def _request(
        self,
        session: requests.Session,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Any] = None,
        timeout: Optional[Tuple[int, int]] = None,
        sleep_sec: Optional[float] = None,
        anti_bot: bool = True,
        api_key: Optional[str] = None,
        api_key_header: str = "Authorization",
        api_key_scheme: str = "Bearer",
        request_kwargs: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Internal request handler with anti-bot protection."""
        # Check host blocking
        if self._host_tracker and self._host_tracker.is_blocked(url):
            if logger:
                logger.warning("%sskipping request to blocked host: %s", log_tag, url)
            return None

        # Prepare parameters with anti-bot randomization
        final_params = dict(params or {})
        if anti_bot and self.config.add_random_param:
            final_params["random"] = random.random()

        # Prepare headers with browser fingerprint rotation
        final_headers = dict(headers or {})
        if anti_bot and self.config.rotate_browser_profile:
            final_headers = merge_browser_profile(final_headers)
        # API-key auth is injected last so a caller-supplied credential
        # header can never be clobbered by fingerprint rotation.
        if api_key:
            value = f"{api_key_scheme} {api_key}" if api_key_scheme else api_key
            final_headers[api_key_header] = value

        # Use custom or configured timeout
        request_timeout = timeout if timeout is not None else self.config.timeout

        # Whole-logical-call wall time (audit duration_ms) — the anti-bot
        # sleep below lands AFTER the audit record, so it is excluded.
        started = time.perf_counter()
        duration_ms: Optional[float] = None

        try:
            extra = dict(request_kwargs or {})
            if method == "get":
                resp = session.get(url, params=final_params, headers=final_headers,
                                   timeout=request_timeout, **extra)
            else:
                resp = session.post(url, params=final_params, data=data, headers=final_headers,
                                    timeout=request_timeout, **extra)
            duration_ms = (time.perf_counter() - started) * 1000.0

            # Handle 4xx errors (potential blocking)
            if 400 <= resp.status_code < 500:
                if self._host_tracker:
                    self._host_tracker.record_error(url, resp.status_code, f"HTTP {resp.status_code}")
                if logger:
                    logger.error("%sHTTP %d for %s", log_tag, resp.status_code, url)
                record_http(url, resp.status_code, is_failed=True,
                            failure_reason=f"HTTP {resp.status_code}",
                            duration_ms=duration_ms,
                            service=self.audit_service)
                return None

            resp.raise_for_status()

            record_http(url, resp.status_code, is_failed=False,
                        duration_ms=duration_ms,
                        service=self.audit_service)

            # Sleep after successful request
            if self.config.enable_sleep:
                self.sleep(sleep_sec=sleep_sec)

            return resp

        except requests.RequestException as e:
            status_code = getattr(e.response, "status_code", None)
            if status_code is not None and 400 <= status_code < 500 and self._host_tracker:
                self._host_tracker.record_error(url, status_code, str(e))
            if logger:
                logger.warning("%sRequest failed: %s", log_tag, e)
            record_http(url, status_code, is_failed=True,
                        failure_reason=f"{type(e).__name__}: {e}",
                        duration_ms=(time.perf_counter() - started) * 1000.0,
                        service=self.audit_service)
            return None
        except Exception as e:  # noqa: BLE001 — non-requests sessions
            # curl_cffi and other requests-like sessions raise their own
            # exception types; audit and suppress exactly like above so a
            # foreign session's failure mode can never differ.
            if logger:
                logger.warning("%sRequest failed: %s: %s", log_tag, type(e).__name__, e)
            record_http(url, None, is_failed=True,
                        failure_reason=f"{type(e).__name__}: {e}",
                        duration_ms=(time.perf_counter() - started) * 1000.0,
                        service=self.audit_service)
            return None

    def sleep(self, sleep_sec: Optional[float] = None) -> None:
        """Sleep with jitter based on configured base sleep duration."""
        if not self.config.enable_sleep:
            return

        base = sleep_sec if sleep_sec is not None else self.config.base_sleep_sec
        if base <= 0:
            return

        jitter = base * self.config.sleep_jitter
        sleep_time = random.uniform(base - jitter, base + jitter)
        time.sleep(max(0, sleep_time))

    def sleep_range(self, min_sec: float, max_sec: float) -> None:
        """Sleep for a random duration between min_sec and max_sec."""
        if not self.config.enable_sleep:
            return

        if min_sec <= 0 and max_sec <= 0:
            return

        sleep_time = random.uniform(min(min_sec, max_sec), max(min_sec, max_sec))
        time.sleep(max(0, sleep_time))

    def is_blocked(self, url: str) -> bool:
        """Check if the host for the given URL is blocked."""
        if self._host_tracker is None:
            return False
        return self._host_tracker.is_blocked(url)

    def unblock(self, url: str) -> None:
        """Unblock the host for the given URL."""
        if self._host_tracker is not None:
            self._host_tracker.unblock(url)

    def record_error(self, url: str, status_code: int, reason: str = "") -> None:
        """Record an error for the host of the given URL."""
        if self._host_tracker is not None:
            self._host_tracker.record_error(url, status_code, reason)

    def get_host_status(self, url: str) -> Optional[HostStatus]:
        """Get the status of the host for the given URL."""
        if self._host_tracker is None:
            return None
        return self._host_tracker.get_status(url)


# ---------------------------------------------------------------------------
# Unified HTTP request functions with anti-bot mechanisms and 4xx detection
#
# Note: These functions are now wrappers around AntiBotProxy for backward
# compatibility. New code should prefer using AntiBotProxy directly.
# ---------------------------------------------------------------------------

def safe_get(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    host_tracker: Optional[HostStatusTracker] = None,
    anti_bot: bool = True,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    audit_service: Optional[str] = None,
) -> Optional[requests.Response]:
    """Backward-compatible wrapper for GET requests with anti-bot protection.

    Implemented using AntiBotProxy internally; no automatic sleeping (callers
    control cadence). New code should prefer AntiBotProxy directly.
    """
    config = AntiBotConfig(
        rotate_browser_profile=anti_bot,
        add_random_param=anti_bot,
        enable_sleep=False,  # Legacy safe_get doesn't sleep automatically
        enable_host_tracking=host_tracker is not None,
        timeout=timeout,
    )
    proxy = AntiBotProxy(config, audit_service=audit_service)

    # If a host_tracker was provided, use it instead of creating a new one
    if host_tracker is not None:
        proxy._host_tracker = host_tracker

    return proxy.get(
        session, url,
        params=params,
        headers=headers,
        anti_bot=anti_bot,
        logger=logger,
        log_tag=log_tag,
    )


def safe_post(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    host_tracker: Optional[HostStatusTracker] = None,
    anti_bot: bool = True,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    audit_service: Optional[str] = None,
) -> Optional[requests.Response]:
    """Backward-compatible wrapper for POST requests with anti-bot protection.

    Implemented using AntiBotProxy internally; no automatic sleeping (callers
    control cadence). New code should prefer AntiBotProxy directly.
    """
    config = AntiBotConfig(
        rotate_browser_profile=anti_bot,
        add_random_param=anti_bot,
        enable_sleep=False,  # Legacy safe_post doesn't sleep automatically
        enable_host_tracking=host_tracker is not None,
        timeout=timeout,
    )
    proxy = AntiBotProxy(config, audit_service=audit_service)

    # If a host_tracker was provided, use it instead of creating a new one
    if host_tracker is not None:
        proxy._host_tracker = host_tracker

    return proxy.post(
        session, url,
        params=params,
        data=data,
        headers=headers,
        anti_bot=anti_bot,
        logger=logger,
        log_tag=log_tag,
    )
