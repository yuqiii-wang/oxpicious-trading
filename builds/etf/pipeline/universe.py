"""Step 5b — per-ETF universe (sec_classification quality stats source)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_array, safe_columns

from builds.etf.theme import classify_etf_theme


def build_universe(
    merged: pd.DataFrame,
    comp_universe: pd.DataFrame | None,
    db_day_stats: pd.DataFrame | None = None,
    exists: np.ndarray | None = None,
) -> pd.DataFrame:
    """Groupby-aggregate per-ETF coverage stats + theme classification.

    All lookups are vectorized joins on frames — no dict hops, no row-wise
    list comprehensions. ``classify_etf_theme`` is an inherently scalar
    keyword-rule classifier (host-only); applied once over one transferred
    column (Series.map(callable) numba-JIT fails under cudf.pandas).

    B3: when the DB history was fetched with a trailing window, the
    merged-derived first_date / last_date / n_ohlcv_days are
    window-truncated. *db_day_stats* (code, db_first, db_last, db_n_days
    from stats.etf_identity) + *exists* (per-row "already in DB" mask from
    the PE scope) restore the true values: first/last take the min/max
    across merged and stored, and the day count is stored rows + merged
    rows not yet in the DB.
    """
    # count() instead of size(): cudf.pandas GroupBy.size falls back on this
    # frame (truth-value ambiguity); count on a non-null column is the same
    # value on the GPU path.
    _g = merged.groupby("code", sort=True)
    sizes = _g["date"].count()
    first_dt = _g["date"].min()
    last_dt = _g["date"].max()

    if "rz_balance" in safe_columns(merged):
        # group by column NAME (grouping by a proxy Series is a cudf
        # transfer-blocking fallback); bool sum → int64 margin-day count
        mg = merged[["code", "rz_balance"]].assign(
            _mg=merged["rz_balance"].fillna(0) > 0)
        n_margin = mg.groupby("code", sort=True)["_mg"].sum().astype("int64")
    else:
        n_margin = sizes * 0

    # ``code`` is carried as an explicit column (never the index) so later
    # merges/joins cannot duplicate it on reset.  ``exchange`` comes straight
    # from the canonical CSV column (downloads guarantees it) — conditions
    # must branch on it, never on code-suffix string ops.
    uni = pd.DataFrame({
        "code": sizes.index.to_series(index=sizes.index),
        "exchange": _g["exchange"].first(),
        "name": _g["name"].first(),
        "n_ohlcv_days": sizes,
        "n_margin_days": n_margin,
        # datetime64 until after the B3 min/max — strings only at the end
        "first_date": first_dt,
        "last_date": last_dt,
    })

    # B3 window correction — see docstring.
    if db_day_stats is not None and len(db_day_stats):
        uni = uni.merge(db_day_stats, on="code", how="left")
        has_db = uni["db_first"].notna()
        # datetime min/max via where with a Series other (np.where would
        # assign an object column — cudf Unsupported-dtype-object fallback)
        take_first = has_db & (uni["db_first"] < uni["first_date"])
        uni["first_date"] = uni["first_date"].where(~take_first, uni["db_first"])
        take_last = has_db & (uni["db_last"] > uni["last_date"])
        uni["last_date"] = uni["last_date"].where(~take_last, uni["db_last"])
        if exists is not None:
            # day count = stored rows + merged rows not yet in the DB
            # (temp column keeps the groupby on the by-name GPU path)
            merged["_is_new"] = ~exists
            n_new = merged.groupby("code", sort=False)["_is_new"].sum()
            merged = merged.drop(columns=["_is_new"])
            uni = uni.merge(
                n_new.rename("n_new").reset_index(), on="code", how="left")
            uni["n_ohlcv_days"] = (
                uni["db_n_days"].fillna(0) + uni["n_new"].fillna(0)
            ).astype("int64")
            uni = uni.drop(columns=["n_new"])
        else:
            uni["n_ohlcv_days"] = uni["db_n_days"].fillna(0).astype("int64")
        uni = uni.drop(columns=["db_first", "db_last", "db_n_days"])
    uni["first_date"] = uni["first_date"].dt.strftime("%Y-%m-%d")
    uni["last_date"] = uni["last_date"].dt.strftime("%Y-%m-%d")

    # Theme classification — host loop over ONE transferred column
    # (Series.map with a python callable numba-JIT-fails under cudf.pandas)
    names_np = np.asarray(host_array(uni["name"].fillna("")))
    th = np.asarray([classify_etf_theme(n) for n in names_np])
    uni["theme_id"] = th[:, 0].tolist()
    uni["theme_label"] = th[:, 1].tolist()
    uni["theme_slug"] = th[:, 2].tolist()

    # Composition coverage — vectorized left-join on whole suffixed codes.
    if comp_universe is not None and len(comp_universe):
        cu = comp_universe.rename(columns={"etf_code": "code"})[
            ["code", "n_dates", "n_holdings_latest"]]
        uni = uni.merge(cu, on="code", how="left")
    uni_cols = safe_columns(uni)
    for src, dst in (("n_dates", "n_comp_dates"),
                     ("n_holdings_latest", "n_holdings_latest")):
        if src in uni_cols:
            uni[dst] = uni[src].fillna(0).astype("int64")
            if dst != src:
                del uni[src]
        else:
            uni[dst] = 0

    return uni.sort_values(["theme_id", "n_ohlcv_days"],
                           ascending=[True, False]).reset_index(drop=True)
