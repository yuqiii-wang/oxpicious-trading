"""SignalStore — the family's table-store mixin of SignalEngine
(analyze.analysis_signals.engines._store).

The family's TWO tables are this mixin's whole concern: the snapshot
write is CHUNKED BY PARTITION KEY (the bulk-write rule — see
_common.db_commons): the records are grouped by ``code`` (both tables
are PARTITION BY HASH (code)), codes accumulate into ~100K-row chunks
(whole codes, never split), and each chunk commits ONE transaction —
purge that code set's rows from BOTH tables, then COPY its records —
with the replica-lag cap paused between chunks. A code is therefore
either fully old or fully new after every commit (atomicity per code;
a crash mid-snapshot leaves whole-code prefixes, and a rerun rewrites
the snapshot idempotently). Mutable-scope snapshots are deleted before
they are re-emitted; truly-missing snapshots need no delete (the
DELETE is a no-op that keeps the write idempotent). purge_sec_type
deletes the sec_type's WHOLE family from both tables, chunked the same
way (retired keys are removed by SnapshotSelection.sweep_stale_
snapshots every incremental run, also chunked).

(The derived-population cohort scoping + the snapshot_emissions
attempts registry went with the forecast layer 2026-10-05: the signals
tier emits the single plain rsi-pct population and resolves coverage
at snapshot granularity.)
"""
from __future__ import annotations

from datetime import date

from _common.db_commons import (
    chunked_dml_by_key_async,
    copy_insert_async,
    _chunk_keys_by_weight,
    _wait_for_replica_lag_async,
    DEFAULT_MAX_REPLICA_LAG_MB,
)
from _common.db_commons._helpers import DEFAULT_COMMIT_CHUNK_ROWS

from analyze.analysis_signals.config import (
    CURVE_COLUMNS,
    HISTORY_COLUMNS,
    THRESHOLD_COLUMNS,
    TABLE_CURVES,
    TABLE_HISTORY,
    TABLE_THRESHOLDS,
)

# ~100K rows per commit chunk (the shared commit-chunk target): a code
# set's threshold + history rows + their purge deletes stay inside one
# ~100K-row transaction, so the replication slot advances between
# chunks instead of retaining the whole snapshot's WAL behind one
# mega-transaction.
_WRITE_CHUNK_ROWS = DEFAULT_COMMIT_CHUNK_ROWS


class SignalStore:
    """write_snapshot / purge_sec_type — the family's own two tables,
    keyed by the engine's signal_type (see module docstring). Requires
    the engine's ``signal_type`` identity attr."""

    signal_type: str

    async def write_snapshot(
        self,
        conn,
        sec_type: str,
        stat_date: date,
        thresholds: list[dict],
        history: list[dict],
        curves: list[dict] | None = None,
    ) -> tuple[int, int, int]:
        """Write one snapshot's threshold + history + curve rows —
        chunked by the partition key (see module docstring): each code
        chunk commits ONE transaction that purges that code set's rows
        from ALL THREE tables and COPYs its records. The history purge
        scope is the snapshot's OWNERSHIP INTERVAL — its CALENDAR YEAR
        (a year-end snapshot owns year Y; the ROLLING LATEST snapshot
        owns [Jan 1, its key] — the key IS the latest data date, so no
        trigger can exist beyond it and the year bound covers the
        interval). Returns (n_thresholds, n_history, n_curves)."""
        curves = curves or []
        if not thresholds and not history and not curves:
            return 0, 0, 0
        thresh_by_code: dict[str, list[dict]] = {}
        for r in thresholds:
            thresh_by_code.setdefault(r["code"], []).append(r)
        hist_by_code: dict[str, list[dict]] = {}
        for r in history:
            hist_by_code.setdefault(r["code"], []).append(r)
        curve_by_code: dict[str, list[dict]] = {}
        for r in curves:
            curve_by_code.setdefault(r["code"], []).append(r)
        weighted = [
            (c, len(thresh_by_code.get(c, ()))
             + len(hist_by_code.get(c, ()))
             + len(curve_by_code.get(c, ())))
            for c in sorted(
                set(thresh_by_code) | set(hist_by_code)
                | set(curve_by_code),
            )
        ]
        first_day, next_year = self._year_bounds(stat_date)
        n_thresholds = n_history = n_curves = 0
        for chunk in _chunk_keys_by_weight(weighted, _WRITE_CHUNK_ROWS):
            # Between commit chunks (no transaction open, no locks):
            # wait out standby replay lag before generating more WAL.
            await _wait_for_replica_lag_async(
                conn, DEFAULT_MAX_REPLICA_LAG_MB,
            )
            chunk_thresh = [r for c in chunk for r in thresh_by_code.get(c, ())]
            chunk_hist = [r for c in chunk for r in hist_by_code.get(c, ())]
            chunk_curve = [r for c in chunk for r in curve_by_code.get(c, ())]
            async with conn.transaction():
                await conn.execute(
                    f"DELETE FROM {TABLE_THRESHOLDS} "
                    f"WHERE sec_type = $1 AND signal_type = $2 "
                    f"AND end_date = $3 AND code = ANY($4::text[])",
                    sec_type, self.signal_type, stat_date, chunk,
                )
                await conn.execute(
                    f"DELETE FROM {TABLE_HISTORY} "
                    f"WHERE sec_type = $1 AND signal_type = $2 "
                    f"AND date >= $3 AND date < $4 "
                    f"AND code = ANY($5::text[])",
                    sec_type, self.signal_type, first_day, next_year, chunk,
                )
                await conn.execute(
                    f"DELETE FROM {TABLE_CURVES} "
                    f"WHERE sec_type = $1 AND signal_type = $2 "
                    f"AND end_date = $3 AND code = ANY($4::text[])",
                    sec_type, self.signal_type, stat_date, chunk,
                )
                # copy_insert_async opens a nested transaction (a
                # savepoint inside this one) — the chunk still commits
                # as ONE unit at the outer boundary.
                await copy_insert_async(
                    conn, TABLE_THRESHOLDS, chunk_thresh,
                    columns=THRESHOLD_COLUMNS,
                )
                await copy_insert_async(
                    conn, TABLE_HISTORY, chunk_hist,
                    columns=HISTORY_COLUMNS,
                )
                await copy_insert_async(
                    conn, TABLE_CURVES, chunk_curve,
                    columns=CURVE_COLUMNS,
                )
            n_thresholds += len(chunk_thresh)
            n_history += len(chunk_hist)
            n_curves += len(chunk_curve)
        return n_thresholds, n_history, n_curves

    async def purge_sec_type(self, conn, sec_type: str) -> None:
        """Delete the sec_type's WHOLE family from all three tables —
        chunked by the partition key (the family's rows are counted per
        code, codes chunked to ~100K-row transactions, each chunk's
        deletes inside one transaction, replica-lag throttle between
        chunks)."""
        weighted = await _family_weights(conn, sec_type, self.signal_type)
        await chunked_dml_by_key_async(
            conn,
            statements=[
                (f"DELETE FROM {TABLE_THRESHOLDS} "
                 f"WHERE sec_type = $1 AND signal_type = $2",
                 " AND code = ANY(${ph}::text[])"),
                (f"DELETE FROM {TABLE_HISTORY} "
                 f"WHERE sec_type = $1 AND signal_type = $2",
                 " AND code = ANY(${ph}::text[])"),
                (f"DELETE FROM {TABLE_CURVES} "
                 f"WHERE sec_type = $1 AND signal_type = $2",
                 " AND code = ANY(${ph}::text[])"),
            ],
            params=(sec_type, self.signal_type),
            weighted_keys=weighted,
        )

    # ---- private helpers --------------------------------------------------------

    def _year_bounds(self, stat_date: date) -> tuple[date, date]:
        """(first day of the snapshot's calendar year, first day of the
        NEXT year) — the half-open date interval the snapshot owns (the
        1-year-stride ownership rule; adjacent snapshot keys own
        adjacent years and never collide on the history PK)."""
        return (date(stat_date.year, 1, 1),
                date(stat_date.year + 1, 1, 1))


async def _family_weights(
    conn, sec_type: str, signal_type: str,
) -> list[tuple[str, int]]:
    """Per-code doomed-row counts across BOTH family tables (the
    chunker's weights — the bigger table's rows dominate the chunk's
    WAL)."""
    weights: dict[str, int] = {}
    for table in (TABLE_THRESHOLDS, TABLE_HISTORY, TABLE_CURVES):
        rows = await conn.fetch(
            f"SELECT code, count(*) AS n FROM {table} "
            f"WHERE sec_type = $1 AND signal_type = $2 GROUP BY code",
            sec_type, signal_type,
        )
        for r in rows:
            weights[r["code"]] = weights.get(r["code"], 0) + r["n"]
    return list(weights.items())
