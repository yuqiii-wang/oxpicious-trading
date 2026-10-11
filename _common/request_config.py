"""Runtime reader for the static external_request_config JSON tree.

database/sql/monitoring/static/external_request_config/ is the repo's
per-(service, request_type) pacing / anti-bot inventory: the dotted
service name is the path (downloads/etf/sse/composition.json ←
downloads.etf.sse.composition), each file holds that service's rows, and
the tree-root default.json is the FALLBACK for services without their own
file (the repo-wide 60s anti-bot cadence).

Until 2026-10-10 the JSON was a browsable inventory only — the runtime
source of truth was net.py's code constants and per-downloader literals.
This module makes the JSON the RUNTIME source: every pacing consumer
(AntiBotProxy construction, sleep constants, poll loops, retry backoffs)
resolves its interval here. Resolution order per (service, request_type):

  1. the service's own file, row with the matching request_type
  2. the service's own file, row with request_type == "default"
  3. default.json, same two-step match
  4. the FALLBACK_* code defaults below (mirroring
     downloads._common.net's DEFAULT_* constants)

Any problem reading (missing file, malformed JSON, bad field type) is
skipped field-by-field / file-by-file — a config problem must never block
a pipeline (same contract as service_heartbeat.max_parallel_for).

Lives in the global ``_common`` (not ``downloads._common``) because the
tree also serves non-downloads services (llm_agents/llm_ask.json).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
_STATIC_EXT_REQUEST_CONFIG_DIR = (
    _REPO_ROOT / "database" / "sql" / "monitoring" / "static"
    / "external_request_config"
)

# Last-resort code defaults, mirroring downloads._common.net's DEFAULT_*
# constants (net.py derives its constants from these so there is ONE
# numeric source). Reached only when neither the service's own file nor
# default.json yields a usable row.
FALLBACK_SLEEP_SEC: float = 20.0
FALLBACK_SLEEP_JITTER: float = 0.5
FALLBACK_TIMEOUT_CONNECT_SEC: float = 15.0
FALLBACK_TIMEOUT_READ_SEC: float = 60.0
FALLBACK_ROTATE_BROWSER_PROFILE: bool = True
FALLBACK_ADD_RANDOM_PARAM: bool = True
FALLBACK_ENABLE_HOST_TRACKING: bool = True


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
    # Where the row came from: "<file>:<request_type>" or "code-defaults".
    source: str


def _num(value: object) -> Optional[float]:
    """JSON number → float, or None when absent/invalid (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _positive(value: object) -> Optional[float]:
    num = _num(value)
    return num if num is not None and num > 0 else None


def _int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _files_for(service: str) -> Tuple[Path, ...]:
    """The service's own tree file first (``downloads.stream.bse.price`` →
    downloads/stream/bse/price.json), then the tree-root default.json
    fallback. A service name that is not a clean dotted module path
    (segments outside [A-Za-z0-9_-], or a ``..`` traversal) skips its own
    file — only the fallback is consulted. Same contract as
    service_heartbeat._service_config_files."""
    files: list[Path] = []
    segments = service.split(".")
    if service and all(
        s and all(c.isalnum() or c in "_-" for c in s) for s in segments
    ):
        files.append(
            _STATIC_EXT_REQUEST_CONFIG_DIR.joinpath(*segments).with_suffix(
                ".json"
            )
        )
    files.append(_STATIC_EXT_REQUEST_CONFIG_DIR / "default.json")
    return tuple(files)


def _rows_for(file: Path) -> list[dict]:
    """The file's config rows, or [] on any read problem."""
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(doc, list):
        return []
    return [row for row in doc if isinstance(row, dict)]


def _match_row(rows: list[dict], request_type: str) -> Optional[dict]:
    for rt in (request_type, "default"):
        for row in rows:
            if row.get("request_type") == rt:
                return row
    return None


def _resolve(service: str, request_type: str) -> RequestConfig:
    for file in _files_for(service):
        row = _match_row(_rows_for(file), request_type)
        if row is None:
            continue
        origin = f"{file.relative_to(_REPO_ROOT)}:{row.get('request_type')}"
        sleep_sec = _positive(row.get("sleep_sec"))
        jitter = _num(row.get("sleep_jitter"))
        connect = _positive(row.get("timeout_connect_sec"))
        read = _positive(row.get("timeout_read_sec"))
        return RequestConfig(
            service=service,
            request_type=request_type,
            sleep_sec=sleep_sec if sleep_sec is not None else FALLBACK_SLEEP_SEC,
            sleep_jitter=(
                jitter if jitter is not None else FALLBACK_SLEEP_JITTER
            ),
            poll_interval_sec=_positive(row.get("poll_interval_sec")),
            max_retries=_int(row.get("max_retries")),
            retry_backoff_sec=_num(row.get("retry_backoff_sec")),
            timeout_connect_sec=connect,
            timeout_read_sec=read,
            rotate_browser_profile=_bool(
                row.get("rotate_browser_profile"),
                FALLBACK_ROTATE_BROWSER_PROFILE,
            ),
            add_random_param=_bool(
                row.get("add_random_param"), FALLBACK_ADD_RANDOM_PARAM
            ),
            enable_host_tracking=_bool(
                row.get("enable_host_tracking"),
                FALLBACK_ENABLE_HOST_TRACKING,
            ),
            host_block_cooldown_sec=_positive(
                row.get("host_block_cooldown_sec")
            ),
            source=origin,
        )
    return RequestConfig(
        service=service,
        request_type=request_type,
        sleep_sec=FALLBACK_SLEEP_SEC,
        sleep_jitter=FALLBACK_SLEEP_JITTER,
        poll_interval_sec=None,
        max_retries=None,
        retry_backoff_sec=None,
        timeout_connect_sec=FALLBACK_TIMEOUT_CONNECT_SEC,
        timeout_read_sec=FALLBACK_TIMEOUT_READ_SEC,
        rotate_browser_profile=FALLBACK_ROTATE_BROWSER_PROFILE,
        add_random_param=FALLBACK_ADD_RANDOM_PARAM,
        enable_host_tracking=FALLBACK_ENABLE_HOST_TRACKING,
        host_block_cooldown_sec=None,
        source="code-defaults",
    )


_cache: Dict[Tuple[str, str], RequestConfig] = {}
_cache_lock = threading.Lock()


def request_config_for(
    service: str, request_type: str = "default"
) -> RequestConfig:
    """Resolve one (service, request_type) row from the static JSON tree.

    Cached per (service, request_type) — the tree is repo-static config,
    read once per process. See the module docstring for resolution order.
    """
    key = (service, request_type)
    with _cache_lock:
        cfg = _cache.get(key)
    if cfg is not None:
        return cfg
    cfg = _resolve(service, request_type)
    with _cache_lock:
        _cache[key] = cfg
    return cfg


def sleep_sec_for(service: str, request_type: str = "default") -> float:
    """The service's inter-request anti-bot sleep (JSON-sourced)."""
    return request_config_for(service, request_type).sleep_sec


def poll_interval_sec_for(
    service: str, request_type: str = "poll"
) -> Optional[float]:
    """The service's stream poll-cycle period, or None when the row does
    not define one (callers keep their code default in that case)."""
    return request_config_for(service, request_type).poll_interval_sec


def host_block_cooldown_for(
    service: str, request_type: str = "default"
) -> Optional[float]:
    """The service's blocked-host auto-unblock delay, or None for the
    HostStatusTracker class default."""
    return request_config_for(service, request_type).host_block_cooldown_sec


def retry_policy_for(
    service: str, request_type: str = "default"
) -> Tuple[Optional[int], Optional[float]]:
    """(max_retries, retry_backoff_sec) from the JSON row; either may be
    None when undefined (retry loops keep their code default)."""
    cfg = request_config_for(service, request_type)
    return cfg.max_retries, cfg.retry_backoff_sec


def clear_request_config_cache() -> None:
    """Drop the resolution cache (tests / config hot-reload)."""
    with _cache_lock:
        _cache.clear()
