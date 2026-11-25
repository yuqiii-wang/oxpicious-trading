"""server_cluster._common.routers.process_detail — the ONE unified
query face (control unification plan Phase 3).

``GET /processes/{service}/detail`` merges, for ONE service, everything
the cluster can say about it:

(a) the supervisor row — when the service is a RESIDENT of this area
    (desired state, restarts, backoff);
(b) the heartbeat row — beat age, scope, the stop record, the
    parallel-slot state, and the code-fingerprint staleness verdict
    (row fingerprint vs a fresh disk hash — the supervisor's
    staleness rule, on demand);
(c) DERIVED progress — the missing-set oracle as progress
    (``expected − missing``, one query per step: the service_config
    ``progress`` block's identity table vs its result table; for the
    stream pollers, the per-asset completeness counts the SSE
    poller's ``get_intraday_progress`` computes — the SAME sql_view
    template the poller's own loop renders, so the SQL lives in one
    place per the SQL-first rule);
(d) REPORTED progress — the Phase 4 columns on the heartbeat row
    (phase machine + state + payload) when the child posts them;
(e) the service-log tail (the same file the child writes).

``GET /processes?detail=1`` returns the resident rows with their
detail payloads folded in — the fleet fold rides this (one call per
area, the same fan-out ``FleetOps`` already does; the fold skips the
per-request disk fingerprint, which is the supervisor staleness pass's
job, and bounds the tails).

Sync ``def`` routes on purpose: the DB reads, the fingerprint hash and
the log tail are blocking work (the maintainer restart route's
rationale — FastAPI runs sync routes in its threadpool).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from _common.labels import CODE_MAPS

from ..config import AreaConfig
from ..data_definitions import DerivedProgress, HeartbeatDetail, \
    ProcessDetail, ResidentStatus
from ..deps import get_config, get_processes, get_supervisor
from ..process_manager import ProcessManager, _log_path_for
from ..resident_supervisor import ResidentSupervisor

logger = logging.getLogger(__name__)

# The Phase 4 progress columns are SMALLINT label codes (the
# label_dim domains progress_phase / progress_state) — decode to labels
# at this API boundary before the pydantic model (str fields) sees
# them. An unknown code degrades to None instead of rejecting the
# whole detail payload (the 2026-11-21 downloader detail rejects).
_PROGRESS_PHASE_LABEL: dict[int, str] = CODE_MAPS["progress_phase"]
_PROGRESS_STATE_LABEL: dict[int, str] = CODE_MAPS["progress_state"]

# The detail fold's tail bound (the full endpoint uses the config's
# log_tail_bytes; a folded fleet poll does not need the whole tail).
_FOLD_TAIL_BYTES = 4096

_HEARTBEAT_DETAIL_SQL = """
SELECT pid,
       EXTRACT(EPOCH FROM (now() - beat_at))::bigint AS beat_age_s,
       started_at, scope, sub_scope,
       stop_reason, is_normal_exit, stopped_at,
       live_parallel, code_fingerprint,
       progress_phase, progress_state, progress_payload, progress_at,
       EXTRACT(EPOCH FROM (now() - progress_at))::bigint AS progress_age_s
FROM monitor.service_heartbeat
WHERE service = $1
"""

# The pre-Phase-4 shape (the progress columns arrive with their DDL; a
# DB not yet altered answers UndefinedColumn and the base query runs).
_HEARTBEAT_BASE_SQL = """
SELECT pid,
       EXTRACT(EPOCH FROM (now() - beat_at))::bigint AS beat_age_s,
       started_at, scope, sub_scope,
       stop_reason, is_normal_exit, stopped_at,
       live_parallel, code_fingerprint
FROM monitor.service_heartbeat
WHERE service = $1
"""

_MISSING_SET_SQL = """
-- The generic missing-set oracle (Phase 3's derived progress): the
-- identity (source) table vs the result table on ONE key column, at
-- the latest date (or the given one). args:
--   identity_table — fragment: the source identity table
--   result_table   — fragment: the result table
--   key_col        — fragment: the shared key column (code / contract_code)
--   key_date_col   — fragment: the shared date column (date)
--   date           — value date|null: NULL picks the identity table's latest
WITH target AS (
    SELECT COALESCE({{date}}::date,
                    (SELECT MAX({{key_date_col}}) FROM {{identity_table}})) AS d
),
expected AS (
    SELECT DISTINCT {{key_col}} AS key
    FROM {{identity_table}} ident, target
    WHERE ident.{{key_date_col}} = target.d
),
done AS (
    SELECT DISTINCT {{key_col}} AS key
    FROM {{result_table}} res, target
    WHERE res.{{key_date_col}} = target.d
)
SELECT target.d AS trade_date,
       (SELECT COUNT(*) FROM expected) AS expected_total,
       (SELECT COUNT(*) FROM (
            SELECT key FROM expected
            EXCEPT
            SELECT key FROM done
        ) missing) AS missing
FROM target
"""


class ProcessDetailBuilder:
    """The detail payload's assembler (ONE per server, mounted on
    app.state via the route module's deps call)."""

    def __init__(self, config: AreaConfig, processes: ProcessManager,
                 supervisor: ResidentSupervisor) -> None:
        self._config = config
        self._processes = processes
        self._supervisor = supervisor

    # ------------------------------------------------------------------
    # the five evidence blocks
    # ------------------------------------------------------------------

    def _resident_row(self, service: str) -> ResidentStatus | None:
        """(a) — the supervisor row when the service is THIS area's
        resident."""
        for row in self._supervisor.snapshot():
            if row.service == service:
                return row
        return None

    def _heartbeat_row(self, service: str, *,
                       include_progress: bool = True) -> HeartbeatDetail | None:
        """(b+d) — one heartbeat-row read (progress columns included
        when the DB has them; a pre-Phase-4 schema degrades to the base
        query). None when the service has no row yet. The read rides
        the asyncpg pool via one asyncio.run (the sql_views render
        carries ``$n`` placeholders — asyncpg's native shape; the
        arrangement module's sync-face precedent)."""
        sql = _HEARTBEAT_DETAIL_SQL if include_progress \
            else _HEARTBEAT_BASE_SQL
        row = self._fetch_row(sql, (service,), degrade=include_progress)
        if row is None:
            return None
        if include_progress and len(row) > 10:
            (pid, beat_age, started_at, scope, sub_scope, stop_reason,
             normal, stopped_at, live_parallel, fingerprint,
             phase, state, payload, progress_at, progress_age) = row
            payload_dict = payload if isinstance(payload, dict) else (
                json.loads(payload) if isinstance(payload, str) else None)
            return HeartbeatDetail(
                service=service, pid=pid,
                started_at=_iso(started_at), beat_age_s=beat_age,
                scope=scope, sub_scope=sub_scope,
                stop_reason=stop_reason, is_normal_exit=normal,
                stopped_at=_iso(stopped_at), live_parallel=live_parallel,
                code_fingerprint=fingerprint,
                progress_phase=_PROGRESS_PHASE_LABEL.get(phase),
                progress_state=_PROGRESS_STATE_LABEL.get(state),
                progress_payload=payload_dict,
                progress_at=_iso(progress_at), progress_age_s=progress_age)
        (pid, beat_age, started_at, scope, sub_scope, stop_reason,
         normal, stopped_at, live_parallel, fingerprint) = row
        return HeartbeatDetail(
            service=service, pid=pid, started_at=_iso(started_at),
            beat_age_s=beat_age, scope=scope, sub_scope=sub_scope,
            stop_reason=stop_reason, is_normal_exit=normal,
            stopped_at=_iso(stopped_at), live_parallel=live_parallel,
            code_fingerprint=fingerprint)

    def _fingerprint_stale(self, service: str,
                           detail: HeartbeatDetail | None) -> bool | None:
        """(b) — the staleness verdict: the row fingerprint vs a FRESH
        disk hash (the supervisor's staleness rule, on demand).
        None = uncheckable (no row, unknown hashes)."""
        if detail is None or not detail.code_fingerprint \
                or detail.code_fingerprint == "unknown":
            return None
        from _common.service_heartbeat import fingerprint_roots_for, \
            source_fingerprint

        disk = source_fingerprint(fingerprint_roots_for(service))
        if disk == "unknown":
            return None
        return disk != detail.code_fingerprint

    def _derived_progress(self, service: str) -> DerivedProgress | None:
        """(c) — the missing-set oracle from the service_config
        ``progress`` block (one query per asset / step). Fail-soft: a
        malformed config or a query problem yields None (the config
        summary still shows the block was intended)."""
        from _common.service_heartbeat import service_progress_config

        block = service_progress_config(service)
        if block is None:
            return None
        try:
            if block["kind"] == "intraday":
                return DerivedProgress(
                    kind="intraday", detail={
                        "assets": [self._intraday_asset(a)
                                   for a in block["assets"]]})
            if block["kind"] == "missing_set":
                return DerivedProgress(
                    kind="missing_set", detail=self._missing_set(
                        block["identity_table"], block["result_table"],
                        block.get("key_column", "code")))
        except Exception as exc:  # noqa: BLE001 — progress is a bonus
            logger.warning("derived progress for %s failed: %s", service,
                           exc)
            return None
        return None

    def _intraday_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        """One poller asset's completeness counts — the SHARED
        intraday_progress template (the same one the SSE poller's loop
        renders; table names ride the service_config args). close_time
        is a real datetime.time (asyncpg binds times strictly)."""
        from datetime import time

        from _common.sql_views import frag, render_sql_view

        rendered = render_sql_view(
            "downloads/stream/sse/price/_io/intraday_progress",
            identity_table=frag(asset["identity_table"]),
            intraday_table=frag(asset["intraday_table"]),
            code_col=frag(asset.get("code_column") or "code"),
            exchange_col=frag(asset.get("exchange_column") or "NULL"),
            exchange=asset.get("exchange"),
            close_time=time(15, 0),
            trade_date=None,
        )
        row = self._fetch_one(rendered)
        keys = ("trade_date", "n_identity", "n_total_codes", "max_time",
                "n_codes_done", "close_reached")
        out = dict(asset)
        out["progress"] = dict(zip(keys, row)) if row is not None else {}
        return out

    def _missing_set(self, identity_table: str, result_table: str,
                     key_column: str) -> dict[str, Any]:
        """One step's expected-minus-missing counts (the generic
        template above)."""
        from _common.sql_views import frag, render_sql_view

        rendered = render_sql_view(
            "server_cluster/_common/routers/process_detail/"
            "missing_set_progress",
            identity_table=frag(identity_table),
            result_table=frag(result_table),
            key_col=frag(key_column),
            key_date_col=frag("date"),
            date=None,
        )
        row = self._fetch_one(rendered)
        keys = ("trade_date", "expected_total", "missing")
        return dict(zip(keys, row)) if row is not None else {}

    @staticmethod
    def _fetch_row(sql: str, params: tuple | list,
                   degrade: bool = False) -> tuple | None:
        """One read → its first row as a plain tuple (asyncpg Record →
        tuple). The query rides the asyncpg pool through ONE
        asyncio.run (sync route face; the arrangement precedent). With
        ``degrade`` the ONE known schema gap (the pre-Phase-4 missing
        progress columns) falls back to the base heartbeat query."""
        import asyncio

        async def _run() -> tuple | None:
            from _common.db_commons import get_db_connection_async

            conn = await get_db_connection_async()
            try:
                try:
                    return tuple(await conn.fetchrow(sql, *params))
                except Exception as exc:  # noqa: BLE001 — the one
                    if not degrade or not _is_missing_column(exc):
                        raise
                    return tuple(await conn.fetchrow(_HEARTBEAT_BASE_SQL,
                                                     *params))
            finally:
                await conn.close()

        return asyncio.run(_run())

    @classmethod
    def _fetch_one(cls, rendered) -> tuple | None:
        """One rendered sql_view read → its first row (see
        _fetch_row; no degrade path — a view is either renderable or
        the caller's fail-soft handles it)."""
        return cls._fetch_row(rendered.sql, rendered.params)

    def _config_summary(self, service: str) -> dict[str, Any]:
        """The service_config entry's control summary (the class gate's
        inputs, the priority, the caps)."""
        from _common.service_heartbeat import is_resident_for, \
            max_parallel_for, max_pause_s_for, pausable_for, \
            priority_for, resource_class_for

        return {
            "resource_class": resource_class_for(service),
            "pausable": pausable_for(service),
            "max_pause_s": max_pause_s_for(service),
            "max_parallel": max_parallel_for(service),
            "priority": priority_for(service),
            "is_resident": is_resident_for(service),
        }

    def _log_tail(self, service: str, n_bytes: int | None) -> str:
        """(e) — the service log's tail (the file the child writes and
        a spawned run redirects into)."""
        try:
            return self._processes.tail(_log_path_for(service),
                                        n_bytes=n_bytes)
        except Exception:  # noqa: BLE001 — a tail is best effort
            return ""

    # ------------------------------------------------------------------
    # assembly
    # ------------------------------------------------------------------

    def build(self, service: str, *, include_fingerprint: bool = True,
              tail_bytes: int | None = None,
              resident_row: ResidentStatus | None = None) -> ProcessDetail:
        """The ONE unified payload for ``service`` (unknown to this area
        is FINE — a batch run's row lives in the heartbeat table, a
        non-resident service simply answers no supervisor row).
        ``resident_row`` lets a caller that already snapshotted the
        supervisor pass its row in (the fold — one liveness probe set
        per request, not one per resident)."""
        heartbeat = self._heartbeat_row(service)
        resident = (resident_row if resident_row is not None
                    else self._resident_row(service))
        stale = (self._fingerprint_stale(service, heartbeat)
                 if include_fingerprint else None)
        if heartbeat is not None and stale is not None:
            heartbeat = heartbeat.model_copy(
                update={"fingerprint_stale": stale})
        return ProcessDetail(
            service=service, area=self._config.area, resident=resident,
            heartbeat=heartbeat,
            derived_progress=self._derived_progress(service),
            config=self._config_summary(service),
            log_tail=self._log_tail(service, tail_bytes),
        )

    def fold(self, rows: list[ResidentStatus] | None = None,
             include_fingerprint: bool = False) -> list[dict]:
        """``GET /processes?detail=1`` / ``GET /processes-detail`` —
        every resident row with its detail payload folded in (ONE call
        per area; the fleet's fan-out shape). ``rows`` passes a
        pre-taken supervisor snapshot through (ONE liveness probe set
        per request — the folding route snapshots once and hands the
        rows here). Skips the disk fingerprint by default (the
        supervisor staleness pass owns it; the fold stays cheap)."""
        folded: list[dict] = []
        for row in (rows if rows is not None
                    else self._supervisor.snapshot()):
            payload = self.build(
                row.service, include_fingerprint=include_fingerprint,
                tail_bytes=_FOLD_TAIL_BYTES, resident_row=row)
            folded.append(payload.model_dump())
        return folded


def _is_missing_column(exc: Exception) -> bool:
    """True when the DB error is the pre-Phase-4 schema's missing
    progress columns (asyncpg UndefinedColumnError / psycopg
    UndefinedColumn)."""
    return type(exc).__name__ in ("UndefinedColumnError", "UndefinedColumn")


def _iso(value: Any) -> str | None:
    """A timestamp column → ISO text (None passes through)."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


router = APIRouter(tags=["processes"])


@router.get("/processes/{service}/detail", response_model=ProcessDetail)
def process_detail(request: Request, service: str) -> ProcessDetail:
    """The ONE unified query face for ONE service (see the module
    docstring for the five merged evidence blocks)."""
    _require_owned_or_known(request, service)
    return _builder(request).build(service)


@router.get("/processes-detail", response_model=list[dict])
def processes_detail(request: Request,
                     fingerprint: bool = Query(
                         default=False,
                         description="Also compute the disk-fingerprint "
                         "staleness verdict per resident (the fold "
                         "skips it by default).")) -> list[dict]:
    """Every resident row with its detail folded (the fleet fold's
    explicit face; ``GET /processes?detail=1`` answers the same)."""
    return _builder(request).fold(include_fingerprint=fingerprint)


def _require_owned_or_known(request: Request, service: str) -> None:
    """A detail request names a service THIS area plausibly serves: a
    module under the area's prefixes, or one with a resident/heartbeat
    row. Anything else is 404 — a typo should not walk the cluster."""
    from ..catalog import owns

    config: AreaConfig = get_config(request)
    if owns(config.module_prefixes, service) or service in {
            r.service for r in config.residents}:
        return
    # Not a module of ours — check for any heartbeat row (an adopted or
    # operator-run child registered here would have one). asyncpg face
    # (the sql-first reads' $n executor; the arrangement precedent).
    import asyncio

    from _common.db_commons import get_db_connection_async

    async def _known() -> bool:
        conn = await get_db_connection_async()
        try:
            return await conn.fetchval(
                "SELECT 1 FROM monitor.service_heartbeat WHERE service = $1",
                service) is not None
        finally:
            await conn.close()

    if not asyncio.run(_known()):
        raise HTTPException(
            status_code=404,
            detail=f"service {service!r} is unknown to the "
                   f"{config.area} area (no module-prefix match, no "
                   "resident, no heartbeat row)")


def _builder(request: Request) -> ProcessDetailBuilder:
    """The builder, assembled per request from the app.state
    collaborators (the deps module's shape)."""
    return ProcessDetailBuilder(get_config(request),
                                get_processes(request),
                                get_supervisor(request))
