"""OI stats — per-expiry OI level/changes per expiry group
(analysis.options_oi_stats).

Dedicated per-REAL-expiry-date stats (NO open-expiry collapse — the
per-expiry tooltips need per-contract-expiry values, which the pooled
open-group collapse cannot represent). The plain (unweighted) OI level
and its session deltas live here; the moneyness-aware positioning views
are the skew data sources (skewness.py, greek_delta.py).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_rolling_agg


# Delta / rolling-max lookback windows, in TRADING SESSIONS (the offsets
# index the underlying's option calendar, not calendar days).
OI_DELTA_WINDOWS = [5, 20]
OI_MAX_WINDOW = 20

# Expiry group key (option_type excluded — the stats pool calls + puts;
# rows are duplicated per option_type at the end for the PK).
_GROUP_KEY = ["underlying_code", "expiry_date"]


def compute_options_oi_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group OI level/changes per real expiry date.

    For each (underlying_code, expiry_date) group and date:
      oi_total     — sum of open_interest over ALL contracts (calls+puts)
                     of the group on the date;
      oi_delta_Wd  — oi_total minus oi_total W trading sessions earlier
                     (NULL when the group has no row at the offset
                     session, e.g. not yet listed);
      oi_max_20d   — max oi_total over the trailing 20 sessions incl.
                     the date (NULL when no record in the window).

    Session alignment: each expiry group is reindexed over the FULL
    session calendar of its underlying (all dates any option of that
    underlying has rows), so shift(W) is exactly W trading sessions
    regardless of the group's own gaps. Calendar scaffold rows (dates
    where the group has no contracts) are dropped before the write.

    Args:
        df: DataFrame with columns:
            date, contract_code, option_type, underlying_code,
            expiry_date, open_interest, underlying_close.

    Returns:
        DataFrame with OI_RESULT_COLUMNS.
    """
    from analyze.options.config import OI_RESULT_COLUMNS

    if df.empty:
        return pd.DataFrame(columns=OI_RESULT_COLUMNS)

    # ---- Step 1: group OI totals per (date, underlying, REAL expiry) --
    oi = (
        df.groupby(["date"] + _GROUP_KEY, as_index=False, sort=False)
        .agg(oi_total=("open_interest", "sum"))
    )

    # ---- Step 2: session-aligned calendar per underlying --------------
    # sessions × expiries grid per underlying; LEFT-joined oi totals leave
    # NaN on the scaffold rows, so the shift/rolling windows below see a
    # continuous session axis (NaN = group had no contracts that session).
    sessions = df[["underlying_code", "date"]].drop_duplicates()
    groups = oi[_GROUP_KEY].drop_duplicates()
    grid = sessions.merge(groups, on="underlying_code", how="inner")
    grid = grid.merge(
        oi, on=["date"] + _GROUP_KEY, how="left",
    ).sort_values(
        _GROUP_KEY + ["date"]
    ).reset_index(drop=True)

    # ---- Step 3: session-offset deltas + trailing max -----------------
    g = grid.groupby(_GROUP_KEY, sort=False)["oi_total"]
    for w in OI_DELTA_WINDOWS:
        grid[f"oi_delta_{w}d"] = grid["oi_total"] - g.shift(w)
    grid["oi_max_20d"] = grouped_rolling_agg(
        grid, _GROUP_KEY, "oi_total",
        window=OI_MAX_WINDOW, min_periods=1, agg="max",
    )

    # ---- Step 4: drop the calendar scaffold rows ----------------------
    # Real rows always carry a (possibly zero) oi_total sum; NaN marks a
    # date where the group had no contracts. max20 may legitimately be
    # non-NaN here (window reaches back to real rows) — dropped with them.
    grid = grid.loc[grid["oi_total"].notna()].reset_index(drop=True)

    # ---- Step 5: duplicate for both CALL and PUT option types --------
    call_rows = grid.copy()
    call_rows["option_type"] = "CALL"
    put_rows = grid.copy()
    put_rows["option_type"] = "PUT"

    result = pd.concat([call_rows, put_rows], ignore_index=True)

    # ---- Step 6: select and order result columns ---------------------
    result = result[OI_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date"]
    ).reset_index(drop=True)

    return result
