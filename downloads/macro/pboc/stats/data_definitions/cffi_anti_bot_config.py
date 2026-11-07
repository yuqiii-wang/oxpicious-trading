"""CffiAntiBotConfig — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.stats._session` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CffiAntiBotConfig:
    """Configuration for the curl_cffi anti-bot session.

    Every value is resolved from the service's external_request_config JSON
    row (request_type ``curl_cffi``) in :func:`build_session` — there is no
    code-side default; a row that omits a pacing field is a config error.
    """
    base_sleep_sec: float
    sleep_jitter: float  # 0..1, multiplied with base_sleep_sec
    max_retries: int
    retry_backoff_sec: float
    timeout: int
    host_block_cooldown_sec: float
    enable_sleep: bool = True
    enable_host_tracking: bool = True
    rotate_profile: bool = True
