"""AntiBotConfig — data definition.

Migrated verbatim from :mod:`downloads._common.net` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
from _common.request_config import FALLBACK_SLEEP_JITTER, FALLBACK_SLEEP_SEC, FALLBACK_TIMEOUT_CONNECT_SEC, FALLBACK_TIMEOUT_READ_SEC


DEFAULT_SLEEP_SEC = FALLBACK_SLEEP_SEC


DEFAULT_TIMEOUT: Tuple[Optional[float], Optional[float]] = (
    FALLBACK_TIMEOUT_CONNECT_SEC, FALLBACK_TIMEOUT_READ_SEC,
)


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
