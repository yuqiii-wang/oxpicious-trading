"""MonthSelection — the incremental month-resolution mixin of
SignalEngine (analyze.analysis_signals.engines._months).

The former run/months.py free function, now a method (the resolution
filters by the family's own bucket and signal_type — engine-owned
state).
"""
from __future__ import annotations

from datetime import date

from analyze.analysis_forecasts.config import TABLE_IDENTITIES
from analyze.analysis_signals.config import (
    REFRESH_MONTHS,
    TABLE_STRATEGIES,
)


class MonthSelection:
    """resolve_months — which stat_months this family (re-)emits for a
    sec_type. Requires the engine's ``bucket`` and ``signal_type``
    identity attrs."""

    bucket: str
    signal_type: str

    async def resolve_months(
        self,
        conn,
        sec_type: str,
        force: bool,
        months: int | None,
    ) -> list[date]:
        """The stat_months to emit for the family, ascending.

        The target months are the snapshot months PRESENT in
        analysis_forecasts' identities registry (bucket-filtered) —
        signals are produced ONLY for months the forecasts already own,
        never re-derived. Optionally capped to the newest N (--months).

        compute = the months MISSING from signal_strategies plus the
        newest REFRESH_MONTHS present months (a month written right after
        month-end carries a permanently truncated mixed row — its 20d
        forward legs were not complete yet — so it is deleted + re-emitted
        on every run, mirroring analysis_forecasts' refresh window).
        --force recomputes every target month (the caller purges first).
        """
        rows = await conn.fetch(
            f"SELECT DISTINCT stat_month FROM {TABLE_IDENTITIES} "
            f"WHERE sec_type = $1 AND bucket = $2 ORDER BY stat_month",
            sec_type, self.bucket,
        )
        present: list[date] = [r["stat_month"] for r in rows]
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
        refresh = set(present[-REFRESH_MONTHS:])
        compute = [m for m in present if m not in have or m in refresh]
        return compute
