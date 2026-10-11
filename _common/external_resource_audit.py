"""external_resource_audit — best-effort request audit for external resources.

Appends one LEAN row per external request to
``monitor.external_resource_audit`` (database/sql/monitoring/
05_external_resource_audit.sql, PRIMARY KEY (service, id)): WHICH
process — service — requested which URL, what HTTP status, at what
time, how long the whole call took (duration_ms, retries included),
failed or not, and a short failure reason. ``date`` is a GENERATED GMT
(UTC) day of created_at — a new date starts at Beijing 08:00 (GMT
midnight), so the Beijing 00:00–08:00 overnight tail counts to the
previous day; the per-day aggregation into
``monitor.external_resource_stats`` is computed by
``python -m database.external_resource_stats``. The two audited
families, both funneled through ONE fire-and-forget writer:

  * HTTP — ``downloads/_common/net.py`` AntiBotProxy (the consolidated
    anti-bot proxy behind every exchange downloader; the legacy
    safe_get / safe_post wrappers route through it too);
  * LLM — ``llm_agents/_core/transport.py`` RetryingJsonClient (the
    retrying JSON POST transport shared by every llm_agents provider).

resource_type / sub_type are SMALLINT label codes (the label-code
contract): domains ``ext_resource``, ``ext_http_source`` (host family,
suffix-matched from the URL), ``ext_llm_provider`` (provider name) —
registered in stats.label_dim and mirrored in _common/labels.py and
data_viz/api/services/label-codes.ts. ``service`` stays TEXT on
purpose (open-ended vocabulary): resolved from the service_heartbeat
registration (peeked through sys.modules so an unregistered process
never imports it — it drags in db_commons and its transitive pandas
import), falling back to the __main__ entry file; a definite name is
cached, an 'unknown' result is re-resolved on the next request.

Never-blocks contract: ``record*`` only enqueues on a bounded queue
(full queue DROPS with a rate-limited warning) and one lazy daemon
writer thread drains it in batches over a persistent autocommit
connection, reconnecting after failures. A DB outage or a slow writer
can never slow or kill an audited request — losing audit rows is always
preferable to perturbing the request path. db_commons is imported
LAZILY inside the writer thread (the cudf.pandas activation-order
rule for pipelines that install the hook before pandas' first import).
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from _common.labels import (
    EXT_HTTP_SOURCE_CODE, EXT_LLM_PROVIDER_CODE, EXT_RESOURCE_CODE,
)

# One row per FINAL request outcome; retries are not itemized (the retry
# loops already log per-attempt lines). created_at keeps its column
# default; date is GENERATED from it — writers never set either.
_INSERT_SQL = """
INSERT INTO monitor.external_resource_audit
    (service, resource_type, sub_type, url, status_code,
     is_failed, failure_reason, duration_ms)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_REPO_ROOT = Path(__file__).resolve().parents[1]

_QUEUE_MAX = 2000          # bounded: a wedged writer drops, never back-pressures
_BATCH_MAX = 200           # rows per executemany round trip
_REASON_MAX = 500          # failure_reason truncation — short reasons only
_WARN_EVERY_S = 1800.0     # rate-limit audit warnings to one line / 30 min

# hostname suffix -> ext_http_source label, FIRST match wins (order
# matters: pbc.gov.cn must beat the generic gov.cn suffix). Mirrors the
# hosts actually used by the downloads tree (audited 2026-10-08).
_HOST_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("pbc.gov.cn", "pboc"),
    ("gov.cn", "gov"),
    ("sse.com.cn", "sse"),
    ("szse.cn", "szse"),
    ("bse.cn", "bse"),
    ("cffex.com.cn", "cffex"),
    ("csindex.com.cn", "csindex"),
    ("cnindex.com.cn", "cnindex"),
    ("chinabond.com.cn", "chinabond"),
    ("shibor.sh.cn", "shibor"),
    ("huatai-pb.com", "huatai"),
    ("eastmoney.com", "eastmoney"),
    ("cninfo.com.cn", "cninfo"),
    ("zhihu.com", "zhihu"),
)

_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=_QUEUE_MAX)
_writer_lock = threading.Lock()
_writer: threading.Thread | None = None
_halt = threading.Event()
_last_warn = 0.0


def _warn(msg: str) -> None:
    # One stderr line per 30 minutes — a DB outage must not flood the
    # service's nohup log (same policy as HeartbeatThread).
    global _last_warn
    now = time.monotonic()
    if now - _last_warn < _WARN_EVERY_S:
        return
    _last_warn = now
    print(f"[external_resource_audit] {msg}", flush=True)


def http_source_code(url: str) -> int:
    """ext_http_source SMALLINT code for a request URL (host suffix match).

    Unknown hosts decode to ``other`` (99) — never raises on odd URLs.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    for suffix, label in _HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return EXT_HTTP_SOURCE_CODE[label]
    return EXT_HTTP_SOURCE_CODE["other"]


def llm_provider_code(provider: str) -> int:
    """ext_llm_provider SMALLINT code for a provider name ('zhipu', ...)."""
    return EXT_LLM_PROVIDER_CODE.get(provider, EXT_LLM_PROVIDER_CODE["other"])


def _lean_url(url: str) -> str:
    """scheme://host/path — the query string is stripped (anti-bot random
    params and long feed query params are audit noise)."""
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
    except ValueError:
        return url


_service_name: str | None = None


def _resolve_service() -> str:
    """The writing process's service name.

    First the service_heartbeat registration — peeked through sys.modules
    so an unregistered process never imports the module (it would drag in
    db_commons and its transitive pandas import, breaking the cudf.pandas
    activation-order rule this module documents). Fallback: the __main__
    entry file relative to the repo root — the same derivation
    autoregister uses, so both paths agree on the name.

    A definite name is cached for the process's life; an 'unknown' result
    is NOT cached — the next audited request re-peeks, so a process whose
    heartbeat registration lands after its first request (the 2026-10-08
    stream restarts all logged 'unknown' that way) still gets its real
    name on every subsequent row. Only processes with no __main__ file
    AND no registration stay 'unknown' (embedded interpreters).
    """
    global _service_name
    if _service_name is not None:
        return _service_name
    hb = sys.modules.get("_common.service_heartbeat")
    name = hb.current_service() if hb is not None else None
    if name is None:
        main_file = getattr(sys.modules.get("__main__"), "__file__", None)
        if main_file:
            try:
                rel = Path(main_file).resolve().relative_to(_REPO_ROOT)
                parts = list(rel.with_suffix("").parts)
                if parts and parts[-1] == "__main__":
                    parts = parts[:-1]
                if parts:
                    name = ".".join(parts)
            except ValueError:
                pass
    if name is not None:
        _service_name = name
    return name or "unknown"


def record(
    resource_type: int, sub_type: int, url: str,
    status_code: int | None, *, is_failed: bool,
    failure_reason: str | None = None,
    duration_ms: float | None = None,
    service: str | None = None,
) -> None:
    """Enqueue one audit row. Never raises, never blocks.

    *service* overrides the process-resolved name — pass it when the
    caller knows the owning pipeline better than the resolver does
    (long-lived runners, embedded interpreters, anything whose
    ``__main__`` is not the pipeline making the request).
    """
    row = (
        service or _resolve_service(), resource_type, sub_type, _lean_url(url),
        status_code, is_failed,
        (failure_reason[:_REASON_MAX] if failure_reason else None),
        (round(duration_ms) if duration_ms is not None else None),
    )
    try:
        _queue.put_nowait(row)
    except queue.Full:
        _warn("queue full — dropping audit rows (writer wedged or DB down?)")
        return
    _ensure_writer()


def record_http(
    url: str, status_code: int | None, *, is_failed: bool,
    failure_reason: str | None = None,
    duration_ms: float | None = None,
    service: str | None = None,
) -> None:
    """Audit one HTTP-download request (AntiBotProxy outcome)."""
    record(EXT_RESOURCE_CODE["http"], http_source_code(url), url,
           status_code, is_failed=is_failed, failure_reason=failure_reason,
           duration_ms=duration_ms, service=service)


def record_llm(
    provider: str, url: str, status_code: int | None, *, is_failed: bool,
    failure_reason: str | None = None,
    duration_ms: float | None = None,
    service: str | None = None,
) -> None:
    """Audit one LLM API request (RetryingJsonClient outcome)."""
    record(EXT_RESOURCE_CODE["llm"], llm_provider_code(provider), url,
           status_code, is_failed=is_failed, failure_reason=failure_reason,
           duration_ms=duration_ms, service=service)


def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _writer_lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(
                name="ext-resource-audit", target=_writer_loop, daemon=True)
            _writer.start()


def _writer_loop() -> None:
    # db_commons (and its transitive pandas import) stays inside the
    # writer thread so importing this module never perturbs a pipeline's
    # cudf.pandas activation order.
    from _common.db_commons import get_db_connection

    conn = None
    while not _halt.is_set():
        try:
            row = _queue.get(timeout=0.5)
        except queue.Empty:
            continue
        batch = [row]
        while len(batch) < _BATCH_MAX:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        if conn is None:
            try:
                conn = get_db_connection()
            except Exception as e:  # noqa: BLE001 — never kill the writer
                _warn(f"connect failed, dropping {len(batch)} row(s): {e}")
                continue
        try:
            with conn.cursor() as cur:
                cur.executemany(_INSERT_SQL, batch)
        except Exception as e:  # noqa: BLE001 — drop the batch, reconnect
            _warn(f"insert failed, dropping {len(batch)} row(s): {e}")
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def flush(timeout_s: float = 5.0) -> bool:
    """Block until the queue is drained (tests / graceful shutdown).
    Returns True when the queue emptied within *timeout_s*. The trailing
    grace covers the writer's in-flight batch (dequeued but not yet
    committed)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not _queue.empty():
        time.sleep(0.05)
    if not _queue.empty():
        return False
    time.sleep(0.3)
    return _queue.empty()
