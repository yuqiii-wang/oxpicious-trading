"""Period-label helpers for the risk pipeline.

Maps a ``datetime.date`` to its year / season / month label so risk rows
can be aggregated per-period. Season convention is calendar quarters:
Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec.
"""
from __future__ import annotations

import datetime


def season_label(d: datetime.date) -> str:
    """Return 'YYYY-Qn' (Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec)."""
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def period_value(d: datetime.date, period_type: str) -> str:
    """Map a date to its period label for the given ``period_type``.

    period_type ∈ {'year', 'season', 'month'} → 'YYYY' / 'YYYY-Qn' /
    'YYYY-MM'. Raises ValueError for an unknown period_type.
    """
    if period_type == "year":
        return str(d.year)
    if period_type == "season":
        return season_label(d)
    if period_type == "month":
        return f"{d.year}-{d.month:02d}"
    raise ValueError(f"Unknown period_type: {period_type}")
