"""SignalStore — the family's table-store mixin of SignalEngine
(analyze.analysis_signals.engines._store).

The family's TWO tables are this mixin's whole concern: ONE
transaction per (sec_type, signal_type, stat_month) — purge the
month's rows from BOTH tables, then COPY the strategy + history
records. Refresh months are deleted before they are re-emitted;
truly-missing months need no
delete (the DELETE is a no-op that keeps the write idempotent). Crash
safety comes from transactional atomicity — a month is never
partially written. purge_sec_type deletes the sec_type's WHOLE family
from both tables (the --force / --metrics-scoped purge — also removes
months that disappeared from analysis_forecasts).
"""
from __future__ import annotations

from datetime import date

from _common.db_commons import copy_insert_async

from analyze.analysis_signals.config import (
    HISTORY_COLUMNS,
    STRATEGY_COLUMNS,
    TABLE_HISTORY,
    TABLE_STRATEGIES,
)


class SignalStore:
    """write_month / purge_sec_type — the family's own two tables,
    keyed by the engine's signal_type (see module docstring). Requires
    the engine's ``signal_type`` identity attr."""

    signal_type: str

    async def write_month(
        self,
        conn,
        sec_type: str,
        month: date,
        strategies: list[dict],
        history: list[dict],
    ) -> tuple[int, int]:
        """Write one month's strategy + history rows atomically — ONE
        transaction: purge the month's rows from BOTH tables, then COPY
        the records. Returns (n_strategies, n_history)."""
        first_day, next_month = self._month_bounds(month)
        async with conn.transaction():
            await conn.execute(
                f"DELETE FROM {TABLE_STRATEGIES} "
                f"WHERE sec_type = $1 AND signal_type = $2 AND end_date = $3",
                sec_type, self.signal_type, month,
            )
            await conn.execute(
                f"DELETE FROM {TABLE_HISTORY} "
                f"WHERE sec_type = $1 AND signal_type = $2 "
                f"AND date >= $3 AND date < $4",
                sec_type, self.signal_type, first_day, next_month,
            )
            await copy_insert_async(
                conn, TABLE_STRATEGIES, strategies, columns=STRATEGY_COLUMNS,
            )
            await copy_insert_async(
                conn, TABLE_HISTORY, history, columns=HISTORY_COLUMNS,
            )
        return len(strategies), len(history)

    async def purge_sec_type(self, conn, sec_type: str) -> None:
        """Delete the sec_type's WHOLE family from both tables (see
        module docstring)."""
        await conn.execute(
            f"DELETE FROM {TABLE_STRATEGIES} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            sec_type, self.signal_type,
        )
        await conn.execute(
            f"DELETE FROM {TABLE_HISTORY} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            sec_type, self.signal_type,
        )

    # ---- private helpers --------------------------------------------------------

    def _month_bounds(self, month: date) -> tuple[date, date]:
        """(first day of the snapshot month, first day of the NEXT month)
        — the half-open date interval the month owns."""
        first = month.replace(day=1)
        if month.month == 12:
            return first, date(month.year + 1, 1, 1)
        return first, date(month.year, month.month + 1, 1)
