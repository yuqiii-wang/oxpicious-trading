"""HTTP/network layer for all downloaders.

Constants (timeouts, sleep cadences, headers, browser profiles), logger
setup, session builders, host-blocking tracking and the unified
AntiBotProxy used by every exchange downloader.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import requests


DEFAULT_TIMEOUT: Tuple[int, int] = (15, 60)

# Shared default sleep seconds between HTTP requests for anti-bot protection.
# Centralized here so the project's anti-bot policy can be changed in one place.
# Individual downloaders may override based on target site's aggressiveness.
DEFAULT_SLEEP_SEC = 20.0
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


_LOGGER_FMT = "%(asctime)s [%(levelname)s] %(message)s"
_LOGGER_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED_LOGGERS: Set[str] = set()


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    if name not in _CONFIGURED_LOGGERS:
        logging.basicConfig(level=level, format=_LOGGER_FMT, datefmt=_LOGGER_DATEFMT)
        logging.getLogger("urllib3.connection").setLevel(logging.ERROR)
        _CONFIGURED_LOGGERS.add(name)
    return logging.getLogger(name)


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
    # A 4xx-blocked host is auto-unblocked after this much quiet time, so a
    # transient rate-limit (e.g. OSS 403 mid-sweep) doesn't poison the whole
    # process run.  If the retry also 4xx's, record_error re-blocks with a
    # fresh cooldown timer.
    BLOCK_COOLDOWN_SEC: float = 600.0
    # HTTP codes that mean genuine anti-bot blocking / rate limiting.  A 404
    # is a legitimate per-resource miss (e.g. an index with no closeweight.xls
    # on the OSS bucket) and must NOT block the whole host.
    BLOCKING_STATUS_CODES: frozenset = frozenset({403, 429})

    def __init__(self):
        self._host_status: Dict[str, HostStatus] = {}

    def is_blocked(self, url: str) -> bool:
        host = self._extract_host(url)
        status = self._host_status.get(host)
        if status is None or not status.blocked:
            return False
        if time.time() - status.last_error_time >= self.BLOCK_COOLDOWN_SEC:
            status.blocked = False
            status.blocked_reason = ""
            logger = setup_logger("host_tracker")
            logger.warning(
                "Host %s auto-unblocked after %.0fs cooldown, resuming requests",
                host, self.BLOCK_COOLDOWN_SEC,
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
    sleep_jitter: float = 0.5
    # Track host blocking (4xx errors)
    enable_host_tracking: bool = True
    # Timeout for requests
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT


class AntiBotProxy:
    """Unified anti-bot proxy that consolidates browser fingerprint rotation,
    sleep with jitter, host blocking detection, and request parameter randomization.

    This class provides a single interface for all anti-bot mechanisms, making
    it easy to configure and use across the entire codebase.

    Example usage:
        proxy = AntiBotProxy(base_sleep_sec=20.0)
        session = requests.Session()

        # Simple GET with anti-bot protection
        resp = proxy.get(session, url, headers=base_headers)

        # POST with custom sleep
        resp = proxy.post(session, url, data=payload, sleep_sec=30.0)

        # Manual sleep after processing
        proxy.sleep()

        # Check if host is blocked
        if proxy.is_blocked(url):
            # Handle blocked host
            pass
    """

    def __init__(self, config: Optional[AntiBotConfig] = None):
        self.config = config or AntiBotConfig()
        self._host_tracker = HostStatusTracker() if self.config.enable_host_tracking else None

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
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Perform a GET request with anti-bot protection."""
        return self._request(
            session, "get", url,
            params=params, headers=headers, data=None,
            timeout=timeout, sleep_sec=sleep_sec,
            anti_bot=anti_bot, logger=logger, log_tag=log_tag,
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
        logger: Optional[logging.Logger] = None,
        log_tag: str = "",
    ) -> Optional[requests.Response]:
        """Perform a POST request with anti-bot protection."""
        return self._request(
            session, "post", url,
            params=params, headers=headers, data=data,
            timeout=timeout, sleep_sec=sleep_sec,
            anti_bot=anti_bot, logger=logger, log_tag=log_tag,
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

        # Use custom or configured timeout
        request_timeout = timeout if timeout is not None else self.config.timeout

        try:
            if method == "get":
                resp = session.get(url, params=final_params, headers=final_headers, timeout=request_timeout)
            else:
                resp = session.post(url, params=final_params, data=data, headers=final_headers, timeout=request_timeout)

            # Handle 4xx errors (potential blocking)
            if 400 <= resp.status_code < 500:
                if self._host_tracker:
                    self._host_tracker.record_error(url, resp.status_code, f"HTTP {resp.status_code}")
                if logger:
                    logger.error("%sHTTP %d for %s", log_tag, resp.status_code, url)
                return None

            resp.raise_for_status()

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
    proxy = AntiBotProxy(config)

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
    proxy = AntiBotProxy(config)

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
