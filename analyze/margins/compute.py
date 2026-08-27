"""Pandas transformations for analyze.margins.

Three compute functions (all REDUCED / moved in the margin cleanup):

  - ``compute_tech_stats``: per-(sec_type, code) regime-detection columns
    on rz_balance (slope_ma5 + zscore_20d) consumed by the margin_changes
    trend episode detection, producing rows for analysis.margin_tech_stats.

  - ``compute_industry_stats``: per-(date, industry_id) SUM aggregation
    of rz_balance / rz_buy across stocks and ETFs in each industry,
    producing rows for analysis.margin_industry_stats.

  - ``compute_index_margin_series``: per-(index_code, date) weighted-
    AVERAGE rongzi margin series (branch 1 stock-based + branch 2
    ETF-proxy), producing rows for the analysis.margin_index_series TABLE
    (the aggregation was moved out of SQL into this pandas vectorization).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg


# ---- Regime-detection windows ---------------------------------------------
# slope_ma5 (5d rolling mean of the daily balance slope) is the
# segmentation signal; slope_ma20 + slope_std20 are INTERNAL intermediates
# for the zscore. Only slope_ma5 + zscore_20d are persisted.
SLOPE_MA_WINDOWS = [5, 20]
SLOPE_STD_WINDOW = 20


# ---------------------------------------------------------------------------
#  Per-(sec_type, code) regime-detection columns
# ---------------------------------------------------------------------------

def compute_tech_stats(df: pd.DataFrame, sec_type: str) -> pd.DataFrame:
    """Compute the margin_balance regime-detection cols per code.

    Input:
      df with columns [code, date, rz_balance, rz_buy] sorted by (code, date).

    Output:
      DataFrame with columns:
        sec_type, code, date,
        margin_balance_slope_ma5, margin_balance_slope_zscore_20d
      Sorted by (sec_type, code, date). One row per (code, date).

    Conventions:
      - INVALID-VALUE RULE (rz_balance == 0 OR NULL → NaN): a 0 rz_balance
        means "no rongzi position" — missing data for rolling purposes.
        Rolling means/stds skip NaN (min_periods=1), matching the
        "skip-the-date-as-a-holiday" semantics.
      - slope: (X[t] - X[t-1]) / X[t-1], NULL on the first date of each
        code or when X[t-1] <= 0 (denominator guard).
      - slope_ma5: rolling(5, min_periods=1).mean() of the daily slope.
      - zscore_20d: (slope - slope_ma20) / slope_std20, NaN when the
        rolling std is NaN or <= 0.
    """
    if df.empty:
        return pd.DataFrame(
            columns=[
                "sec_type", "code", "date",
                "margin_balance_slope_ma5",
                "margin_balance_slope_zscore_20d",
            ]
        )

    work = df.sort_values(["code", "date"]).reset_index(drop=True)
    grp_keys = ["code"]

    # ---- Invalid-value cleaning: 0 / NULL → NaN ----------------------
    cleaned = pd.to_numeric(work["rz_balance"], errors="coerce")
    work["rz_balance"] = cleaned.where(cleaned > 0)

    # ---- slope = (X[t] - X[t-1]) / X[t-1] ----------------------------
    from _common.df_utils import grouped_shift
    prev_col = "__prev_rz_balance"
    grouped_shift(
        work, grp_keys, ["rz_balance"], [prev_col],
        periods=1, sort=False,
    )
    prev_safe = work[prev_col]
    work["__slope"] = (
        (work["rz_balance"] - prev_safe) / prev_safe
    ).where(prev_safe > 0)
    work.drop(columns=[prev_col], inplace=True)

    # ---- slope_ma5 / slope_ma20 — rolling mean of the daily slope ----
    for w in SLOPE_MA_WINDOWS:
        work[f"__slope_ma{w}"] = grouped_rolling_agg(
            work, grp_keys, "__slope",
            window=w, min_periods=1, agg="mean", sort=False,
        )

    # ---- slope_std20 — rolling SAMPLE std (ddof=1) -------------------
    work["__slope_std20"] = grouped_rolling_agg(
        work, grp_keys, "__slope",
        window=SLOPE_STD_WINDOW, min_periods=1, agg="std", ddof=1,
        sort=False,
    )

    # ---- zscore_20d = (slope - slope_ma20) / slope_std20 --------------
    std_safe = work["__slope_std20"]
    work["__zscore"] = (
        (work["__slope"] - work["__slope_ma20"]) / std_safe
    ).where(std_safe > 0)

    # ---- assemble output ---------------------------------------------
    out = pd.DataFrame(
        {
            "sec_type": sec_type,
            "code": work["code"],
            "date": work["date"],
            "margin_balance_slope_ma5": work["__slope_ma5"],
            "margin_balance_slope_zscore_20d": work["__zscore"],
        }
    )
    return out.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
#  Per-(date, industry_id) SUM aggregation
# ---------------------------------------------------------------------------

def compute_industry_stats(
    etf_tech: pd.DataFrame,
    stock_tech: pd.DataFrame,
    etf_industry_map: pd.DataFrame,
    stock_industry_map: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate per-(sec_type, code, date) rz_balance / rz_buy into
    per-(date, industry_id) SUMs split by stock / etf.

    The inputs are the RAW rz_balance / rz_buy per (code, date) — NOT the
    tech-stats output. Pass the raw history DataFrames from
    fetch.fetch_margin_history.

    Inputs:
      etf_tech    — DataFrame [code, date, rz_balance, rz_buy] for ETFs
      stock_tech  — DataFrame [code, date, rz_balance, rz_buy] for stocks
      etf_industry_map   — DataFrame [code, industry_id, industry_label, ...]
      stock_industry_map — DataFrame [code, industry_id, industry_label, ...]

    Output:
      DataFrame with columns matching analysis.margin_industry_stats:
      [date, industry_id, industry_label, stock_margin_balance,
      etf_margin_balance, stock_margin_buy, etf_margin_buy].
      One row per (date, industry_id).
    """
    out_frames: list[pd.DataFrame] = []

    for hist_df, map_df, prefix in [
        (stock_tech, stock_industry_map, "stock"),
        (etf_tech, etf_industry_map, "etf"),
    ]:
        if hist_df.empty or map_df.empty:
            continue

        # Join history with industry mapping — drops codes with no
        # industry (e.g. ETFs tracking BROAD indices).
        merged = hist_df.merge(
            map_df[["code", "industry_id", "industry_label"]],
            on="code",
            how="inner",
        )
        if merged.empty:
            continue

        # SUM treats 0/NULL as no contribution — fillna(0) is sufficient.
        merged["rz_balance"] = pd.to_numeric(
            merged["rz_balance"], errors="coerce"
        ).fillna(0)
        merged["rz_buy"] = pd.to_numeric(
            merged["rz_buy"], errors="coerce"
        ).fillna(0)

        agg = merged.groupby(
            ["date", "industry_id", "industry_label"], as_index=False
        ).agg(
            **{
                f"{prefix}_margin_balance": ("rz_balance", "sum"),
                f"{prefix}_margin_buy": ("rz_buy", "sum"),
            }
        )
        out_frames.append(agg)

    if not out_frames:
        return pd.DataFrame(
            columns=[
                "date", "industry_id", "industry_label",
                "stock_margin_balance", "etf_margin_balance",
                "stock_margin_buy", "etf_margin_buy",
            ]
        )

    # Outer-join the stock and etf aggregations on (date, industry_id).
    # An industry might have stocks but no ETFs (or vice versa) on a given
    # date — fill missing side with 0.
    stock_agg = next(
        (f for f in out_frames if "stock_margin_balance" in f.columns), None
    )
    etf_agg = next(
        (f for f in out_frames if "etf_margin_balance" in f.columns), None
    )

    if stock_agg is not None and etf_agg is not None:
        merged = stock_agg.merge(
            etf_agg,
            on=["date", "industry_id", "industry_label"],
            how="outer",
        )
    elif stock_agg is not None:
        merged = stock_agg
        merged["etf_margin_balance"] = 0.0
        merged["etf_margin_buy"] = 0.0
    else:
        merged = etf_agg  # type: ignore[assignment]
        merged["stock_margin_balance"] = 0.0
        merged["stock_margin_buy"] = 0.0

    # Fill NaN sums with 0 (outer join produces NaN where one side is
    # missing). industry_label may be NaN if both sides miss — fillna('').
    for c in ("stock_margin_balance", "etf_margin_balance",
              "stock_margin_buy", "etf_margin_buy"):
        merged[c] = merged[c].fillna(0)
    merged["industry_label"] = merged["industry_label"].fillna("")

    # Reorder columns to match DB schema.
    column_order = [
        "date", "industry_id", "industry_label",
        "stock_margin_balance", "etf_margin_balance",
        "stock_margin_buy", "etf_margin_buy",
    ]
    return merged[column_order].sort_values(
        ["date", "industry_id"]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
#  Per-(index_code, date) weighted-average index margin series
# ---------------------------------------------------------------------------

_INDEX_SERIES_COLUMNS = [
    "index_code", "industry_id", "date",
    "index_margin_balance", "index_margin_buy",
    "n_constituents", "n_with_balance",
]


def compute_index_margin_series(
    stock_margin: pd.DataFrame,
    etf_margin: pd.DataFrame,
    classification: pd.DataFrame,
) -> pd.DataFrame:
    """Compute the per-(index_code, date) weighted-AVERAGE rongzi margin
    series — the Python-vectorized replacement for the former
    margin_index_series VIEW aggregation.

    Branch 1 (stock-based): indices with stock constituents —
      index_margin_* = Σ(rz_* × w) / Σ(w) over constituents with
      rz_* > 0, restricted to classification rows with w > 0.

    Branch 2 (ETF-proxy): index codes with NO stock constituents
    (broad-market / strategy indices) — weighted-average of their
    TRACKING ETFs' margin with w = COALESCE(parent_index_weight, 1.0).

    INVALID-VALUE EXCLUSION: constituents with rz_* = 0 / NULL are
    excluded from BOTH numerator AND denominator (NaN > 0 → False).

    Inputs:
      stock_margin   — DataFrame[code, date, rz_balance, rz_buy] (ALL codes).
      etf_margin     — same shape.
      classification — DataFrame[code, type, parent_index_code,
                       parent_index_weight, parent_index_is_primary,
                       industry_id].

    Output:
      DataFrame with columns matching analysis.margin_index_series:
      [index_code, industry_id, date, index_margin_balance,
      index_margin_buy, n_constituents, n_with_balance]. One row per
      (index_code, date). Sorted by (index_code, date).
    """
    empty = pd.DataFrame(columns=_INDEX_SERIES_COLUMNS)
    if classification.empty:
        return empty

    stock_cls = classification[classification["type"] == "stock"]
    etf_cls = classification[classification["type"] == "etf"]
    idx_cls = classification[classification["type"] == "index"]

    # ---- Branch 1: stock-based (weight must be > 0) ------------------
    sc = stock_cls[
        stock_cls["parent_index_code"].notna()
        & (stock_cls["parent_index_code"] != "")
        & stock_cls["parent_index_weight"].notna()
        & (stock_cls["parent_index_weight"] > 0)
    ][["code", "parent_index_code", "parent_index_weight"]]

    # ---- Global set of ALL stock parent codes (branch-2 exclusion) ----
    # From the classification table itself (NOT from the joined margin
    # rows) so the branch routing matches the former VIEW exactly, even
    # when only a date-subset of margin rows is fetched.
    stock_parents_mask = (
        stock_cls["parent_index_code"].notna()
        & (stock_cls["parent_index_code"] != "")
    )
    stock_parents = set(
        np.asarray(stock_cls.loc[stock_parents_mask, "parent_index_code"]).tolist()
    )

    # ---- Branch 2: ETF-proxy (weight COALESCE 1.0) -------------------
    ec = etf_cls[
        (etf_cls["parent_index_is_primary"] == True)  # noqa: E712
        & etf_cls["parent_index_code"].notna()
        & (etf_cls["parent_index_code"] != "")
    ][["code", "parent_index_code", "parent_index_weight"]].copy()
    ec["parent_index_weight"] = ec["parent_index_weight"].fillna(1.0)

    # ---- Attach parent (+ weight) to margin rows, per branch ----------
    _margin_cols = ["code", "date", "rz_balance", "rz_buy"]
    sides: list[pd.DataFrame] = []
    if not stock_margin.empty and not sc.empty:
        sides.append(stock_margin[_margin_cols].merge(sc, on="code", how="inner"))
    if not etf_margin.empty and not ec.empty:
        etf_side = etf_margin[_margin_cols].merge(ec, on="code", how="inner")
        # ETF-proxy only for parents with NO stock constituents.
        etf_side = etf_side[
            ~etf_side["parent_index_code"].isin(list(stock_parents))
        ]
        sides.append(etf_side)

    if not sides:
        return empty

    both = pd.concat(sides, ignore_index=True)
    if both.empty:
        return empty

    # ---- Weighted-average per (index_code, date) — vectorized --------
    w = both["parent_index_weight"]
    bal_valid = both["rz_balance"] > 0
    buy_valid = both["rz_buy"] > 0

    both["_bal_num"] = (both["rz_balance"] * w).where(bal_valid, 0.0)
    both["_bal_den"] = w.where(bal_valid, 0.0)
    both["_buy_num"] = (both["rz_buy"] * w).where(buy_valid, 0.0)
    both["_buy_den"] = w.where(buy_valid, 0.0)
    both["_bal_valid"] = bal_valid.astype(int)

    agg = both.groupby(["parent_index_code", "date"], as_index=False).agg(
        bal_num=("_bal_num", "sum"),
        bal_den=("_bal_den", "sum"),
        buy_num=("_buy_num", "sum"),
        buy_den=("_buy_den", "sum"),
        n_constituents=("code", "count"),
        n_with_balance=("_bal_valid", "sum"),
    )

    # Ratio = num / den where den > 0 (NULLIF semantics).
    agg["index_margin_balance"] = (
        agg["bal_num"] / agg["bal_den"]
    ).where(agg["bal_den"] > 0)
    agg["index_margin_buy"] = (
        agg["buy_num"] / agg["buy_den"]
    ).where(agg["buy_den"] > 0)

    # ---- Attach industry_id from the index's own classification -----
    idx_map = idx_cls[["code", "industry_id"]].drop_duplicates(subset=["code"])
    out = agg.merge(
        idx_map,
        left_on="parent_index_code",
        right_on="code",
        how="left",
    )

    out = pd.DataFrame({
        "index_code": out["parent_index_code"],
        "industry_id": out["industry_id"],
        "date": out["date"],
        "index_margin_balance": out["index_margin_balance"],
        "index_margin_buy": out["index_margin_buy"],
        "n_constituents": out["n_constituents"].astype(int),
        "n_with_balance": out["n_with_balance"].astype(int),
    })
    return out.sort_values(["index_code", "date"]).reset_index(drop=True)
