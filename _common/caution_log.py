"""caution_log — the cross-service caution trail (monitor.caution_log).

ONE row per OPEN issue per service for every caution the system hits:
a running service erroring (an abnormal pipeline exit, an orchestration
tier step failure, a maintenance job crash), an AI run flagging
something suspicious (output-validation complaints that survive the
correction round, a ``[CRITICAL]``-flagged finding, a below-floor Jev
decision), or a pending bug noted by hand (``python -m
_common.caution_log``, the dev-skill rule). The Monitor UI's Cautions
tab reads the table (GET /api/monitor/cautions) and resolves rows.

source / severity_level are SMALLINT label codes (the label-code
contract): domains ``caution_source`` (0 service | 1 ai | 2 manual) and
``caution_severity`` (0 info | 1 warning | 2 error | 3 critical) —
registered in static.label_dim and mirrored in _common/labels.py and
data_viz/api/services/infra/label-codes.ts. ``service`` stays TEXT on
purpose (open-ended vocabulary), resolved exactly the way
external_resource_audit resolves it (heartbeat registration peeked
through sys.modules, falling back to the __main__ entry file).

DEDUPE: writers upsert ON CONFLICT (service, dedupe_key) WHERE NOT
is_resolved — a recurring issue bumps occurrence_count / last_seen_at
on its one OPEN row instead of flooding the table. Resolving closes the
row (is_resolved = true, resolved_timestamp set, from the UI); a
recurrence after resolve inserts a FRESH row, signalling the resolved
issue came back.

LOG CONTEXT: every row carries ``detail["log_context"]`` — how to
LOCATE the error in the logs: the day's service log file
(``logs/<YYYY-MM-DD>/<service>.log``, the log_setup one-file-per-
(date, service) contract keyed by the SAME dotted service name), an
optional grep ``keyword``, and the ``time_from``/``time_to`` range to
narrow to (both default to the record time). CautionEntry stamps the
derived default; a writer-supplied ``detail["log_context"]`` wins, the
manual CLI exposes ``--log-file/--log-keyword/--log-from/--log-to``,
and the data_viz API's POST /cautions takes a ``logContext`` body
field stored under the same key.

Never-blocks contract (the external_resource_audit shape): ``record*``
only validates the entry and enqueues on a bounded queue (a full queue
DROPS with a rate-limited warning) and one lazy daemon writer thread
drains it in batches over a persistent autocommit connection,
reconnecting after failures. A DB outage can never slow or kill the
code path being audited — losing caution rows is always preferable.
db_commons is imported LAZILY inside the writer thread (the cudf.pandas
activation-order rule for pipelines that import this module before
pandas' first import).

The data_viz API writes the same table from TypeScript through
data_viz/api/services/caution-log.service.ts (the
database/sql_writes/data_viz/api/caution_log/ action files) — this
module is the Python-side twin.
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from _common.labels import CAUTION_SEVERITY_CODE, CAUTION_SOURCE_CODE

# One upsert per caution: insert the first hit of an issue, bump the open
# row on every recurrence. created_at / last_seen_at keep their column
# defaults (insert sets both via now()); date is GENERATED — writers
# never set either.
_UPSERT_SQL = """
INSERT INTO monitor.caution_log
    (service, source, severity_level, title, detail, dedupe_key)
VALUES (%s, %s, %s, %s, %s::jsonb, %s)
ON CONFLICT (service, dedupe_key) WHERE NOT is_resolved DO UPDATE SET
    severity_level  = EXCLUDED.severity_level,
    title           = EXCLUDED.title,
    detail          = EXCLUDED.detail,
    occurrence_count = monitor.caution_log.occurrence_count + 1,
    last_seen_at    = now()
"""

_QUEUE_MAX = 500           # bounded: a wedged writer drops, never back-pressures
_BATCH_MAX = 100           # rows per executemany round trip
_TITLE_MAX = 300           # title truncation — the tab's table-row text
_DETAIL_MAX = 16000        # serialized detail cap — tails, never whole logs
_DEDUPE_MAX = 300          # dedupe signature cap
_WARN_EVERY_S = 1800.0     # rate-limit cautions-about-cautions to 1 line / 30 min


class CautionEntry(BaseModel):
    """One caution row crossing the module boundary — validated against
    the label dicts at the record() call, dumped to the row tuple at the
    write boundary. Callers pass label NAMES (source='service',
    severity='error'); the codes are resolved here so a typo fails fast
    instead of writing an undecodable row."""

    service: str
    source: str
    severity: str
    title: str
    dedupe_key: str
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def _check_source(cls, v: str) -> str:
        if v not in CAUTION_SOURCE_CODE:
            raise ValueError(f"unknown caution_source label {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def _check_severity(cls, v: str) -> str:
        if v not in CAUTION_SEVERITY_CODE:
            raise ValueError(f"unknown caution_severity label {v!r}")
        return v

    @model_validator(mode="after")
    def _attach_log_context(self) -> "CautionEntry":
        """Stamp ``detail["log_context"]`` when the writer didn't supply
        one — every caution then carries HOW TO LOCATE ITS LOG LINES:
        the day's service log file and the record time as both range
        bounds. The file derivation rides the log_setup contract (one
        file per (date, service) at ``logs/<YYYY-MM-DD>/<service>.log``,
        keyed by the SAME dotted service name this row carries —
        including a *service* override, since a caution recorded ABOUT
        another area points at THAT area's log). ``keyword`` stays to
        explicit callers (the CLI flags / an HTTP logContext / a
        writer-built detail): nothing in an arbitrary error text is
        guaranteed to appear in the log."""
        if "log_context" in self.detail:
            return self
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.detail["log_context"] = {
            "file": f"logs/{now[:10]}/{self.service}.log",
            "time_from": now,
            "time_to": now,
        }
        return self

    def row(self) -> tuple:
        """The executemany row tuple: label names resolved to codes,
        title/dedupe_key truncated, detail serialized (an oversized
        detail collapses to a stub rather than rejecting the caution)."""
        detail_json = json.dumps(self.detail, default=str, ensure_ascii=False)
        if len(detail_json) > _DETAIL_MAX:
            detail_json = json.dumps(
                {"truncated": True, "title": self.title[:_TITLE_MAX],
                 "keys": sorted(self.detail)[:20]},
                ensure_ascii=False)
        return (
            self.service[:_TITLE_MAX],
            CAUTION_SOURCE_CODE[self.source],
            CAUTION_SEVERITY_CODE[self.severity],
            self.title[:_TITLE_MAX],
            detail_json,
            self.dedupe_key[:_DEDUPE_MAX],
        )


def _resolve_service() -> str:
    """The writing process's service name — the SAME derivation as
    external_resource_audit (heartbeat registration via sys.modules peek,
    __main__ entry-file fallback), imported from there so the two audit
    writers can never disagree on the vocabulary."""
    from _common.external_resource_audit import _resolve_service as _era_resolve
    return _era_resolve()


class CautionLog:
    """The per-process caution recorder: a bounded queue + one lazy daemon
    writer thread (the external_resource_audit shape). record() validates
    a CautionEntry and enqueues its row — never raises past validation,
    never blocks; the writer drains batches over a persistent autocommit
    connection, dropping (with a rate-limited warning) when the DB is
    down rather than perturbing the audited code path."""

    def __init__(self, queue_max: int = _QUEUE_MAX) -> None:
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=queue_max)
        self._writer_lock = threading.Lock()
        self._writer: threading.Thread | None = None
        self._halt = threading.Event()
        self._last_warn = 0.0

    def _warn(self, msg: str) -> None:
        # One stderr line per 30 minutes — a DB outage must not flood the
        # service's log (same policy as external_resource_audit).
        now = time.monotonic()
        if now - self._last_warn < _WARN_EVERY_S:
            return
        self._last_warn = now
        print(f"[caution_log] {msg}", flush=True)

    def record(
        self, source: str, severity: str, title: str, *, dedupe_key: str,
        detail: dict[str, Any] | None = None, service: str | None = None,
    ) -> None:
        """Enqueue one caution row. Never raises, never blocks.

        *service* overrides the process-resolved name — pass it when the
        caution is ABOUT another area than this process (the orchestrator
        recording a step's pipeline, a maintenance job, the decision
        engine naming its lane).
        """
        try:
            row = CautionEntry(
                service=service or _resolve_service(),
                source=source, severity=severity, title=title,
                dedupe_key=dedupe_key or title, detail=detail or {},
            ).row()
        except Exception as e:  # noqa: BLE001 — a bad caution never raises
            self._warn(f"invalid caution dropped ({title[:80]}): {e}")
            return
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            self._warn("queue full — dropping caution rows (writer wedged or DB down?)")
            return
        self._ensure_writer()

    def record_service_error(
        self, title: str, *, dedupe_key: str,
        detail: dict[str, Any] | None = None, service: str | None = None,
        severity: str = "error",
    ) -> None:
        """A running service hit an error (source=service)."""
        self.record("service", severity, title, dedupe_key=dedupe_key,
                    detail=detail, service=service)

    def record_ai_suspicion(
        self, title: str, *, dedupe_key: str,
        detail: dict[str, Any] | None = None, service: str | None = None,
        severity: str = "warning",
    ) -> None:
        """An AI run found something suspicious (source=ai)."""
        self.record("ai", severity, title, dedupe_key=dedupe_key,
                    detail=detail, service=service)

    def _ensure_writer(self) -> None:
        if self._writer is not None and self._writer.is_alive():
            return
        with self._writer_lock:
            if self._writer is None or not self._writer.is_alive():
                self._writer = threading.Thread(
                    name="caution-log", target=self._writer_loop, daemon=True)
                self._writer.start()

    def _writer_loop(self) -> None:
        # db_commons (and its transitive pandas import) stays inside the
        # writer thread so importing this module never perturbs a
        # pipeline's cudf.pandas activation order.
        from _common.db_commons import get_db_connection

        conn = None
        while not self._halt.is_set():
            try:
                row = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            batch = [row]
            while len(batch) < _BATCH_MAX:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if conn is None:
                try:
                    conn = get_db_connection()
                except Exception as e:  # noqa: BLE001 — never kill the writer
                    self._warn(f"connect failed, dropping {len(batch)} row(s): {e}")
                    continue
            try:
                with conn.cursor() as cur:
                    cur.executemany(_UPSERT_SQL, batch)
            except Exception as e:  # noqa: BLE001 — drop the batch, reconnect
                self._warn(f"upsert failed, dropping {len(batch)} row(s): {e}")
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

    def flush(self, timeout_s: float = 5.0) -> bool:
        """Block until the queue is drained (tests / graceful shutdown /
        the manual CLI, which must outlive its daemon writer). Returns
        True when the queue emptied within *timeout_s*."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self._queue.empty():
            time.sleep(0.05)
        if not self._queue.empty():
            return False
        time.sleep(0.3)
        return self._queue.empty()


# The per-process singleton every hook records through.
_RECORDER = CautionLog()


def record(
    source: str, severity: str, title: str, *, dedupe_key: str,
    detail: dict[str, Any] | None = None, service: str | None = None,
) -> None:
    """Enqueue one caution row on the process recorder (see
    CautionLog.record). Never raises, never blocks."""
    _RECORDER.record(source, severity, title, dedupe_key=dedupe_key,
                     detail=detail, service=service)


def record_service_error(
    title: str, *, dedupe_key: str,
    detail: dict[str, Any] | None = None, service: str | None = None,
    severity: str = "error",
) -> None:
    _RECORDER.record_service_error(title, dedupe_key=dedupe_key,
                                   detail=detail, service=service,
                                   severity=severity)


def record_ai_suspicion(
    title: str, *, dedupe_key: str,
    detail: dict[str, Any] | None = None, service: str | None = None,
    severity: str = "warning",
) -> None:
    _RECORDER.record_ai_suspicion(title, dedupe_key=dedupe_key,
                                  detail=detail, service=service,
                                  severity=severity)


def flush(timeout_s: float = 5.0) -> bool:
    """Drain the recorder's queue (tests / graceful shutdown)."""
    return _RECORDER.flush(timeout_s)


def _main(argv: list[str] | None = None) -> int:
    """The MANUAL entry point — the dev-skill rule: a pending bug found
    while running anything by hand gets a caution row (source=manual) so
    it surfaces in Monitor → Cautions until resolved there. Plain CLI, no
    cudf.pandas activation: no frame work happens here (db_commons'
    transitive pandas import inside the writer thread is fine outside a
    pipeline).

    python -m _common.caution_log --service builds.index.daily \\
        --severity warning --title "daily dedupe misses the 000991 tail" \\
        [--detail-json '{"issue": "..."}'] [--dedupe-key ...] \\
        [--log-file logs/2026-11-20/builds.index.daily.log \\
         --log-keyword "000991" --log-from "09:31" --log-to "09:35"]

    Every row auto-carries detail.log_context (the day's service log +
    the record time); the --log-* flags OVERRIDE it for an issue found
    at another time / by another keyword.
    """
    parser = argparse.ArgumentParser(
        prog="python -m _common.caution_log",
        description="Record a MANUAL caution row (a pending bug) into "
                    "monitor.caution_log.")
    parser.add_argument("--service", required=True,
                        help="the service/area the bug concerns "
                             "(dotted module path, e.g. builds.index.daily)")
    parser.add_argument("--severity", required=True,
                        choices=sorted(CAUTION_SEVERITY_CODE),
                        help="caution_severity label")
    parser.add_argument("--title", required=True,
                        help="one-line issue summary")
    parser.add_argument("--detail-json", default=None,
                        help="optional JSON object with context")
    parser.add_argument("--dedupe-key", default=None,
                        help="stable issue signature (default: the title)")
    parser.add_argument("--log-file", default=None,
                        help="log_context override: the log file to search "
                             "(default: logs/<today>/<service>.log)")
    parser.add_argument("--log-keyword", default=None,
                        help="log_context: the grep keyword locating the "
                             "error lines in the log file")
    parser.add_argument("--log-from", default=None,
                        help="log_context: time range start narrowing the "
                             "search (free text, e.g. '09:31')")
    parser.add_argument("--log-to", default=None,
                        help="log_context: time range end")
    args = parser.parse_args(argv)

    detail: dict[str, Any] = {}
    if args.detail_json:
        try:
            parsed = json.loads(args.detail_json)
        except json.JSONDecodeError as e:
            print(f"detail-json is not valid JSON: {e}", file=sys.stderr)
            return 2
        if not isinstance(parsed, dict):
            print("detail-json must be a JSON object", file=sys.stderr)
            return 2
        detail = parsed

    # Explicit --log-* context wins over both the auto-attached default
    # and a detail-json log_context (the flags are the documented knob).
    log_ctx: dict[str, Any] = {}
    if args.log_file:
        log_ctx["file"] = args.log_file
    if args.log_keyword:
        log_ctx["keyword"] = args.log_keyword
    if args.log_from:
        log_ctx["time_from"] = args.log_from
    if args.log_to:
        log_ctx["time_to"] = args.log_to
    if log_ctx:
        detail["log_context"] = log_ctx

    record("manual", args.severity, args.title, service=args.service,
           dedupe_key=args.dedupe_key or args.title, detail=detail)
    if not flush(timeout_s=10.0):
        print("caution row NOT written (writer stalled / DB down)",
              file=sys.stderr)
        return 1
    print(f"caution recorded: [{args.severity}] {args.service}: {args.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
