"""SnapshotSelection — the incremental snapshot-resolution mixin of
SignalEngine (analyze.analysis_signals.engines._months).

The former run/months.py free function, then MonthSelection — now
snapshot-keyed end to end: the annual grid's ROLLING LATEST snapshot
(keyed at the sec_type's latest available data date) plus the mutable
scope rule shared with analysis_forecasts (config.mutable_dates).
"""
from __future__ import annotations

from datetime import date

from _common.db_commons import chunked_dml_by_key_async

from analyze.analysis_forecasts.config import (
    TABLE_IDENTITIES,
    mutable_dates,
)
from analyze.analysis_signals.config import (
    TABLE_HISTORY,
    TABLE_STRATEGIES,
)


class SnapshotSelection:
    """resolve_snapshots / sweep_stale_snapshots — which stat_dates
    this family (re-)emits for a sec_type, and which retired keys to
    purge. Requires the engine's ``bucket`` and ``signal_type``
    identity attrs."""

    bucket: str
    signal_type: str

    async def resolve_snapshots(
        self,
        conn,
        sec_type: str,
        force: bool,
        months: int | None,
    ) -> list[date]:
        """The stat_dates to emit for the family, ascending.

        The target snapshots are the stat_dates PRESENT in
        analysis_forecasts' identities registry (bucket-filtered) —
        signals are produced ONLY for snapshots the forecasts already
        own, never re-derived. Optionally capped to the newest N
        (--months).

        compute = the snapshots MISSING from signal_strategies plus the
        MUTABLE SCOPE among the present ones (config.mutable_dates —
        the ROLLING LATEST snapshot, always, plus the newest completed
        year-end while its 20-trading-day forward windows are still
        unrealized). A snapshot written while its long-horizon mixed
        legs were incomplete is deleted + re-emitted on every run until
        it freezes; completed year-ends outside the mutable scope are
        all-history and never re-emitted. --force recomputes every
        target snapshot (the caller purges first).
        """
        rows = await conn.fetch(
            f"SELECT DISTINCT stat_date FROM {TABLE_IDENTITIES} "
            f"WHERE sec_type = $1 AND bucket = $2 ORDER BY stat_date",
            sec_type, self.bucket,
        )
        present: list[date] = [r["stat_date"] for r in rows]
        if months is not None:
            present = present[-months:]

        if force:
            return present

        written_rows = await conn.fetch(
            f"SELECT DISTINCT end_date FROM {TABLE_STRATEGIES} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            sec_type, self.signal_type,
        )
        have: set[date] = {r["end_date"] for r in written_rows}
        refresh = mutable_dates(set(present), date.today())
        compute = [m for m in present if m not in have or m in refresh]
        return compute

    async def sweep_stale_snapshots(self, conn, sec_type: str) -> int:
        """Delete the family's rows whose end_date is NOT a stat_date
        present in analysis_forecasts' identities registry — the
        ROLLING LATEST key's predecessor (yesterday's key once today's
        has taken over) and any legacy keys. Runs BEFORE
        resolve_snapshots so a retired key can neither suppress a
        re-emission (it counts as 'have') nor linger. The retired
        snapshot's HISTORY rows live in its calendar year (the
        ownership interval) — deleted with it. The deletes are CHUNKED
        BY PARTITION KEY (the bulk-write rule's delete flavor): the
        retired scope's rows are counted per code and deleted in
        ~100K-row whole-code transactions (both tables of a chunk in
        one transaction), replica-lag throttle between chunks. Returns
        the number of retired snapshot keys."""
        id_rows = await conn.fetch(
            f"SELECT DISTINCT stat_date FROM {TABLE_IDENTITIES} "
            f"WHERE sec_type = $1 AND bucket = $2",
            sec_type, self.bucket,
        )
        present: set[date] = {r["stat_date"] for r in id_rows}
        have_rows = await conn.fetch(
            f"SELECT DISTINCT end_date FROM {TABLE_STRATEGIES} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            sec_type, self.signal_type,
        )
        stale = sorted({r["end_date"] for r in have_rows} - present)
        for d in stale:
            first = date(d.year, 1, 1)
            next_year = date(d.year + 1, 1, 1)
            # per-code doomed-row counts across BOTH tables (the
            # chunker's weights; each query carries its own args)
            weights: dict[str, int] = {}
            for sql, args in (
                (f"SELECT code, count(*) AS n FROM {TABLE_STRATEGIES} "
                 f"WHERE sec_type = $1 AND signal_type = $2 "
                 f"AND end_date = $3 GROUP BY code",
                 (sec_type, self.signal_type, d)),
                (f"SELECT code, count(*) AS n FROM {TABLE_HISTORY} "
                 f"WHERE sec_type = $1 AND signal_type = $2 "
                 f"AND date >= $3 AND date < $4 GROUP BY code",
                 (sec_type, self.signal_type, first, next_year)),
            ):
                for r in await conn.fetch(sql, *args):
                    weights[r["code"]] = weights.get(r["code"], 0) + r["n"]
            await chunked_dml_by_key_async(
                conn,
                statements=[
                    (f"DELETE FROM {TABLE_STRATEGIES} "
                     f"WHERE sec_type = $1 AND signal_type = $2 "
                     f"AND end_date = $3",
                     " AND code = ANY(${ph}::text[])",
                     (sec_type, self.signal_type, d)),
                    (f"DELETE FROM {TABLE_HISTORY} "
                     f"WHERE sec_type = $1 AND signal_type = $2 "
                     f"AND date >= $3 AND date < $4",
                     " AND code = ANY(${ph}::text[])",
                     (sec_type, self.signal_type, first, next_year)),
                ],
                params=(),
                weighted_keys=list(weights.items()),
            )
        return len(stale)
