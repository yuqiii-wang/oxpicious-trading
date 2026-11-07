"""RequestConfig — data definition.

Migrated verbatim from :mod:`_common.request_config` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class RequestConfig:
    """One resolved (service, request_type) external-request pacing row."""

    service: str
    request_type: str
    # Jittered anti-bot sleep applied after each successful request.
    sleep_sec: float
    # 0.0 = no jitter, 1.0 = ±100% around sleep_sec.
    sleep_jitter: float
    # Stream pollers: target sweep-cycle period (null = not defined).
    poll_interval_sec: Optional[float]
    # Retry policy for ad-hoc retry loops (null = loop keeps code default).
    max_retries: Optional[int]
    retry_backoff_sec: Optional[float]
    # (connect, read) request timeout; None connect = no connect timeout.
    timeout_connect_sec: Optional[float]
    timeout_read_sec: Optional[float]
    rotate_browser_profile: bool
    add_random_param: bool
    enable_host_tracking: bool
    # Auto-unblock delay for a 4xx-blocked host (null = tracker default).
    host_block_cooldown_sec: Optional[float]
    # Playwright flows: settle wait after page navigation before the next
    # page interaction (null = the flow keeps its own behavior).
    settle_sleep_sec: Optional[float]
    # The row's endpoint inventory: {role: url}. Resolved ONLY from the
    # service's own file (the default.json fallback carries no urls — a
    # fallback URL is meaningless); {} when the row omits it. Values are
    # FULL literal URLs and may carry ``{placeholder}`` templates the
    # module formats at call time.
    urls: Dict[str, str]
    # Where the row came from: "<file>:<request_type>" or "code-defaults".
    source: str
