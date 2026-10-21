"""recent_breach — the is_recently_breached flag columns.

For every (method, window) estimate pair, ``breached_{m}_{w}d`` marks
TRUE when ALL of the last ESTIMATE_BREACH_SPAN_DAYS trading days closed
BEYOND the estimate LINE AS IT STOOD that many trading days ago (the
same column's value ``n`` rows earlier within the code): roof side —
every one of the span's closes above the fixed d−n roof line — or floor
side — every one below the fixed d−n floor line. Rows with fewer than
``n`` rows of history or a NULL d−n level stay FALSE: a NaN line
compares False under float64 semantics, so the flag needs a real level
on the right side of it on the span's extremes. NaN closes are skipped
by the rolling extreme (the same NaN-skipping convention as every
window rule in this suite).

Because the span line is the FIXED d−n level, "every one of the last n
closes beyond it" reduces to ONE rolling-extreme comparison: the span's
MIN close above the roof line / MAX close below the floor line. Two
whole-frame grouped rolling extremes of the close (shared across every
method × window) + one whole-frame grouped shift of the estimate
columns + elementwise comparisons — GPU under cudf.pandas. The flags
ride the frame as int64 0/1 (the label-code contract for flag columns);
the long-frame melt casts them to python bool at the DB boundary.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_BREACH_SPAN_DAYS,
    ESTIMATE_BREACHED_WIDE_COLUMNS,
    ESTIMATE_METHOD_ORDER,
    ESTIMATE_WINDOWS,
)


def attach_recent_breach_flags(
    df: pd.DataFrame,
    group_keys: list[str],
    windows=ESTIMATE_WINDOWS,
) -> None:
    """Assign ``breached_{m}_{w}d`` (int64 0/1) for every method × window.

    Args:
        df: step frame with the estimate columns already attached, the
            ``price`` close column present, sorted by
            [sec_type, code, date] (the sort=False grouped-op contract).
        group_keys: the per-code partition keys (["sec_type", "code"]).
        windows: the estimate windows the columns were computed for.
    """
    if df.empty:
        for col in ESTIMATE_BREACHED_WIDE_COLUMNS:
            df[col] = pd.Series(dtype="int64")
        return

    n = ESTIMATE_BREACH_SPAN_DAYS

    # ONE span-extreme pair for the whole (method × window) grid:
    # min/max close over the last n rows per code (min_periods=n — a
    # shorter history stays NaN → the comparisons below stay False).
    span_lo = grouped_rolling_agg(
        df, group_keys, "price", n, min_periods=n, agg="min", sort=False,
    )
    span_hi = grouped_rolling_agg(
        df, group_keys, "price", n, min_periods=n, agg="max", sort=False,
    )

    # The d−n line per estimate column — ONE whole-frame grouped shift
    # (NaN on each code's first n rows and where the estimate itself is
    # NaN → the flag column stays 0).
    est_cols: list[str] = [
        f"{side}_{m}_{w}d"
        for w in windows
        for m in ESTIMATE_METHOD_ORDER
        for side in ("roof", "floor")
    ]
    helpers = [f"_line{n}_{c}" for c in est_cols]
    grouped_shift(df, group_keys, est_cols, helpers, periods=n, sort=False)

    for w in windows:
        for m in ESTIMATE_METHOD_ORDER:
            breached = (
                (span_lo > df[f"_line{n}_roof_{m}_{w}d"])
                | (span_hi < df[f"_line{n}_floor_{m}_{w}d"])
            )
            df[f"breached_{m}_{w}d"] = breached.astype("int64")

    for c in helpers:
        del df[c]
