"""Pandas transformations for analyze.margins.

Two compute functions:

  - ``compute_tech_stats``: per-(sec_type, code) ma5/ma20/ma60 + slope
    on rz_balance and rz_buy, PLUS regime-detection cols (slope_ma5/ma20,
    slope_std20, zscore_20d) consumed by the margin_changes step, producing
    rows for analysis.margin_tech_stats.

  - ``compute_industry_stats``: per-(date, industry_id) SUM aggregation
    of rz_balance / rz_buy across stocks and ETFs in each industry,
    producing rows for analysis.margin_industry_stats. Includes
    *_margin_count (actively rongzi-traded subset) and
    stock_margin_weight_share (SUM of parent_index_weight for the
    actively-traded subset).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import (
    grouped_diff,
    grouped_rolling_agg,
    should_use_gpu,
)

from analyze.margins.config import MA_WINDOWS


# ---- Regime-detection windows (for slope_ma5 / slope_ma20 / slope_ma255) --
# Fixed at 5 / 20 / 255 to match the SQL column names (slope_ma5,
# slope_ma20, slope_ma255, slope_std20, zscore_20d). The z-score
# baseline = slope_ma20; the std window = 20d (sample std, ddof=1).
# slope_ma255 is the long-term (~1 trading year) trend baseline.
# See 12_margin.sql + 13_margin_changes.sql.
SLOPE_MA_WINDOWS = [5, 20]
SLOPE_MA255_WINDOW = 255
SLOPE_STD_WINDOW = 20


# ---------------------------------------------------------------------------
#  Per-(sec_type, code) technical indicators
# ---------------------------------------------------------------------------

def compute_tech_stats(df: pd.DataFrame, sec_type: str) -> pd.DataFrame:
    """Compute ma5/ma20/ma60 + slope + regime-detection cols on rz_balance
    and rz_buy per code.

    Input:
      df with columns [code, date, rz_balance, rz_buy] sorted by (code, date).

    Output:
      DataFrame with columns:
        sec_type, code, date,
        margin_balance_ma5, margin_balance_ma20, margin_balance_ma60,
        margin_balance_slope,
        margin_balance_slope_ma5, margin_balance_slope_ma20,
        margin_balance_slope_std20, margin_balance_slope_zscore_20d,
        margin_buy_ma5, margin_buy_ma20, margin_buy_ma60,
        margin_buy_slope,
        margin_buy_slope_ma5, margin_buy_slope_ma20,
        margin_buy_slope_std20, margin_buy_slope_zscore_20d
      Sorted by (sec_type, code, date). One row per (code, date).

    Conventions (mirror stats.etf_tech_stats / mov_ave_spreads_detail):
      - INVALID-VALUE RULE (rz_balance / rz_buy == 0 OR NULL → treated
        as missing data, NOT as a real zero). On the source tables a 0
        rz_balance means "no rongzi position" / "no rongzi buy flow" —
        these are missing data points for MA / slope purposes (40% of
        stock rows and 84% of ETF rows are 0; including them in a rolling
        mean drags the MA toward 0 and corrupts the slope denominator).
        So:
          * rz_balance / rz_buy == 0 OR NULL → NaN in the working frame
            BEFORE any rolling computation.
          * MA: pandas rolling(W, min_periods=1).mean() per code on the
            NaN-aware series — pandas .mean() skips NaN by default, so
            the rolling mean = mean of the NON-NaN (i.e. genuinely
            non-zero) values in the window. min_periods=1 still gives
            a partial MA for the first W-1 valid values of each code.
            This is the "skip-the-date-as-a-holiday" semantics: zero
            days don't count toward the denominator (like weekends /
            holidays that have no row at all).
          * The MA column itself is MASKED to NaN on days where the
            source value is NaN (== 0 or NULL) — i.e. on a zero-rz day
            the MA is also NULL (entry val NULL for zero / null data),
            but the MA on the next valid day still uses the last W
            non-zero values from the rolling window.
      - slope: fractional day-over-day change (X[t] - X[t-1]) / X[t-1],
        NULL on the first date of each code or when X[t-1] <= 0
        (denominator guard — a zero prior balance / flow would otherwise
        produce +/-inf). With the zero→NaN rule above, X[t-1] is NaN on
        a zero-rz day, so prev_safe > 0 evaluates False and slope = NaN
        (the day is "skipped as a holiday" for slope purposes too).
      - slope_ma5/ma20: rolling(W, min_periods=1).mean() of the daily
        slope per code — smooths 1-day noise (ma5) and gives the medium-
        term trend (ma20). NaN where the slope is NaN (first date of
        code); otherwise partial mean for the first W-1 valid slopes.
      - slope_std20: rolling(20, min_periods=1).std(ddof=1) — SAMPLE std.
        NaN for the first 2 rows of each code (sample std needs >= 2
        valid values).
      - zscore_20d: (slope - slope_ma20) / slope_std20. NULL (NaN) when
        slope_std20 is NaN or <= 0 (flat / no variance — significance
        undefined). Consumed by analyze.margins.changes for UP/DOWN
        regime classification + hype-episode pairing.
    """
    if df.empty:
        return pd.DataFrame(
            columns=[
                "sec_type", "code", "date",
                "margin_balance_ma5", "margin_balance_ma20",
                "margin_balance_ma60", "margin_balance_slope",
                "margin_balance_slope_ma5", "margin_balance_slope_ma20",
                "margin_balance_slope_std20", "margin_balance_slope_zscore_20d",
                "margin_buy_ma5", "margin_buy_ma20",
                "margin_buy_ma60", "margin_buy_slope",
                "margin_buy_slope_ma5", "margin_buy_slope_ma20",
                "margin_buy_slope_std20", "margin_buy_slope_zscore_20d",
            ]
        )

    work = df.sort_values(["code", "date"]).reset_index(drop=True)
    grp_keys = ["code"]

    # ---- Invalid-value cleaning: 0 / NULL → NaN ----------------------
    # A 0 rz_balance / rz_buy is missing data (no rongzi position), NOT
    # a real zero. Treating it as NaN makes the rolling mean skip it
    # (pandas .mean() skipna=True default) and masks the MA output to
    # NULL on those days — matching the "skip the date as a holiday"
    # semantics. Apply per-code so the cleaning doesn't bleed across
    # code boundaries (it wouldn't anyway since rolling is per-code, but
    # explicit per-code cleaning is clearer).
    for src_col in ("rz_balance", "rz_buy"):
        cleaned = pd.to_numeric(work[src_col], errors="coerce")
        # 0 → NaN. Negative rz_balance shouldn't happen (it's an
        # outstanding balance), but defensively treat <0 as NaN too.
        work[src_col] = cleaned.where(cleaned > 0)

    # ---- MA5 / MA20 / MA60 for rz_balance (margin_balance) -----------
    # Computed on the NaN-aware series. pandas rolling().mean() skips
    # NaN by default, so the MA = mean of the non-NaN (genuinely
    # non-zero) values in the window. min_periods=1 → partial MA for
    # the first W-1 valid values per code (NOT the first W-1 calendar
    # rows — zero days don't count toward min_periods either).
    for w in MA_WINDOWS:
        work[f"margin_balance_ma{w}"] = grouped_rolling_agg(
            work, grp_keys, "rz_balance",
            window=w, min_periods=1, agg="mean", sort=False,
        )

    # ---- MA5 / MA20 / MA60 for rz_buy (margin_buy) -------------------
    for w in MA_WINDOWS:
        work[f"margin_buy_ma{w}"] = grouped_rolling_agg(
            work, grp_keys, "rz_buy",
            window=w, min_periods=1, agg="mean", sort=False,
        )

    # ---- Mask MA to NaN on days where the source is NaN --------------
    # The rolling mean skips NaN source values, so on a zero-rz day the
    # MA still gets a value (mean of the last W non-zero values). Per
    # spec ("entry val NULL if zero or null data"), mask the MA output
    # to NaN on those days — the MA is NULL on a zero-rz day, but the
    # next valid day's MA still correctly uses the rolling window of
    # non-zero values.
    for w in MA_WINDOWS:
        work[f"margin_balance_ma{w}"] = work[f"margin_balance_ma{w}"].where(
            work["rz_balance"].notna()
        )
        work[f"margin_buy_ma{w}"] = work[f"margin_buy_ma{w}"].where(
            work["rz_buy"].notna()
        )

    # ---- slope = (X[t] - X[t-1]) / X[t-1] ----------------------------
    # Compute X[t-1] via grouped_shift, then (X - X_prev) / X_prev.
    # NULL when X_prev is NULL (first date of code) or X_prev <= 0
    # (denominator guard — avoids +/-inf).
    from _common.df_utils import grouped_shift
    for src_col, slope_col in [
        ("rz_balance", "margin_balance_slope"),
        ("rz_buy", "margin_buy_slope"),
    ]:
        prev_col = f"__prev_{src_col}"
        # grouped_shift with periods=1 gives the previous row's value
        # within each code group.
        grouped_shift(
            work, grp_keys, [src_col], [prev_col],
            periods=1, sort=False,
        )
        # (X - X_prev) / X_prev, NULL when X_prev is NULL or <= 0.
        prev_safe = work[prev_col]
        # Use where() to mask out invalid denominators — produces NaN
        # which asyncpg serializes as SQL NULL.
        work[slope_col] = (
            (work[src_col] - prev_safe) / prev_safe
        ).where(prev_safe > 0)
        work.drop(columns=[prev_col], inplace=True)

    # ---- Regime-detection cols (slope_ma5/ma20/ma255/std20/zscore) ---
    # Computed per (sec_type, code) on the daily slope. slope_ma5/ma20
    # smooth 1-day noise + give the medium-term trend; slope_ma255 is
    # the long-term (~1 trading year) trend baseline; slope_std20 is the
    # rolling sample std (ddof=1) used as the z-score denominator;
    # zscore = (slope - slope_ma20) / slope_std20, NaN when std <= 0.
    # Consumed by analyze.margins.changes for UP/DOWN trend classification
    # + significance filtering (zscore sign must match trend direction
    # for ALL trend days).
    for slope_col, prefix in [
        ("margin_balance_slope", "margin_balance"),
        ("margin_buy_slope", "margin_buy"),
    ]:
        # slope_ma5 / slope_ma20 — rolling mean of the daily slope.
        for w in SLOPE_MA_WINDOWS:
            work[f"{prefix}_slope_ma{w}"] = grouped_rolling_agg(
                work, grp_keys, slope_col,
                window=w, min_periods=1, agg="mean", sort=False,
            )
        # slope_std20 — rolling SAMPLE std (ddof=1) of the daily slope.
        std_col = f"{prefix}_slope_std20"
        work[std_col] = grouped_rolling_agg(
            work, grp_keys, slope_col,
            window=SLOPE_STD_WINDOW, min_periods=1, agg="std", ddof=1,
            sort=False,
        )
        # zscore_20d = (slope - slope_ma20) / slope_std20. NaN when
        # slope_std20 is NaN or <= 0 (flat history — no variance to
        # measure anomaly against).
        ma20_col = f"{prefix}_slope_ma20"
        zscore_col = f"{prefix}_slope_zscore_20d"
        std_safe = work[std_col]
        work[zscore_col] = (
            (work[slope_col] - work[ma20_col]) / std_safe
        ).where(std_safe > 0)

    # margin_balance_slope_ma255 — 255-day rolling mean of the balance
    # slope (long-term trend baseline, ~1 trading year). Only computed
    # for the balance (not buy) — the SQL column is balance-only.
    work["margin_balance_slope_ma255"] = grouped_rolling_agg(
        work, grp_keys, "margin_balance_slope",
        window=SLOPE_MA255_WINDOW, min_periods=1, agg="mean", sort=False,
    )

    # ---- assemble output ---------------------------------------------
    out = pd.DataFrame(
        {
            "sec_type": sec_type,
            "code": work["code"],
            "date": work["date"],
            "margin_balance_ma5": work["margin_balance_ma5"],
            "margin_balance_ma20": work["margin_balance_ma20"],
            "margin_balance_ma60": work["margin_balance_ma60"],
            "margin_balance_slope": work["margin_balance_slope"],
            "margin_balance_slope_ma5": work["margin_balance_slope_ma5"],
            "margin_balance_slope_ma20": work["margin_balance_slope_ma20"],
            "margin_balance_slope_ma255": work["margin_balance_slope_ma255"],
            "margin_balance_slope_std20": work["margin_balance_slope_std20"],
            "margin_balance_slope_zscore_20d": work["margin_balance_slope_zscore_20d"],
            "margin_buy_ma5": work["margin_buy_ma5"],
            "margin_buy_ma20": work["margin_buy_ma20"],
            "margin_buy_ma60": work["margin_buy_ma60"],
            "margin_buy_slope": work["margin_buy_slope"],
            "margin_buy_slope_ma5": work["margin_buy_slope_ma5"],
            "margin_buy_slope_ma20": work["margin_buy_slope_ma20"],
            "margin_buy_slope_std20": work["margin_buy_slope_std20"],
            "margin_buy_slope_zscore_20d": work["margin_buy_slope_zscore_20d"],
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
    per-(date, industry_id) SUMs split by stock / etf, plus counts.

    The inputs are the RAW rz_balance / rz_buy per (code, date) — NOT the
    tech-stats output (which has MAs but not the raw values). Pass the
    raw history DataFrames from fetch.fetch_margin_history.

    Inputs:
      etf_tech    — DataFrame [code, date, rz_balance, rz_buy] for ETFs
      stock_tech  — DataFrame [code, date, rz_balance, rz_buy] for stocks
      etf_industry_map   — DataFrame [code, industry_id, industry_label,
                            parent_index_weight] for ETFs
      stock_industry_map — DataFrame [code, industry_id, industry_label,
                            parent_index_weight] for stocks

    Output:
      DataFrame with columns matching analysis.margin_industry_stats
      (excluding the GENERATED columns total_margin_balance,
      total_margin_buy, *_margin_count_share — those are computed by
      the DB). One row per (date, industry_id).

    Conventions:
      - stock_count = COUNT of stocks in this industry on this date
        (includes stocks with rz_balance = 0 — i.e. all stocks that have
        a margin row on this date, even if rongzi is 0).
      - stock_margin_count = subset with rz_balance > 0 on this date.
      - stock_margin_weight_share = SUM of parent_index_weight across
        the actively-rongzi-traded subset. Populated by Python (NOT
        GENERATED — depends on per-stock parent-index weight).
      - stock_margin_balance = SUM(rz_balance) across ALL stocks in
        the industry on this date (including 0-rongzi stocks; SUM is
        unaffected by 0s).
      - stock_margin_buy = SUM(rz_buy) similarly.
      - (etf_* equivalents)
    """
    out_frames: list[pd.DataFrame] = []

    for sec_type, hist_df, map_df, prefix in [
        ("stock", stock_tech, stock_industry_map, "stock"),
        ("etf", etf_tech, etf_industry_map, "etf"),
    ]:
        if hist_df.empty or map_df.empty:
            continue

        # Join history with industry mapping — drops codes with no
        # industry (e.g. ETFs tracking BROAD indices).
        merged = hist_df.merge(
            map_df[["code", "industry_id", "industry_label",
                    "parent_index_weight"]],
            on="code",
            how="inner",
        )
        if merged.empty:
            continue

        # Indicator: rz_balance > 0 (actively rongzi-traded on this date).
        # rz_balance is always non-negative (it's an outstanding balance);
        # 0 means no rongzi position. NULL would mean the source row is
        # missing rz_balance, but the source tables have NOT NULL DEFAULT
        # 0 on rz_balance, so NULL never occurs in practice. Defensive
        # fillna(0) just in case.
        merged["rz_balance"] = pd.to_numeric(
            merged["rz_balance"], errors="coerce"
        ).fillna(0)
        merged["rz_buy"] = pd.to_numeric(
            merged["rz_buy"], errors="coerce"
        ).fillna(0)
        merged["has_rongzi"] = (merged["rz_balance"] > 0).astype(int)
        # Pre-fill parent_index_weight so the groupby lambda doesn't need
        # a per-group fillna (avoids the pandas FutureWarning about
        # downcasting object dtype on fillna). parent_index_weight may be
        # NULL for ETFs (only stocks have weights from sec_composition);
        # filling with 0 makes the SUM 0 for ETFs, which is acceptable
        # (weight_share is meaningful for stocks only).
        merged["parent_index_weight"] = pd.to_numeric(
            merged["parent_index_weight"], errors="coerce"
        ).fillna(0)
        # Weighted indicator: parent_index_weight * has_rongzi — pre-computed
        # so the groupby can use a plain sum instead of a lambda (faster +
        # warning-free).
        merged["__weight_x_rongzi"] = (
            merged["parent_index_weight"] * merged["has_rongzi"]
        )

        # Per (date, industry_id) aggregation:
        #   count = COUNT of codes (all, including rz_balance=0)
        #   margin_count = SUM(has_rongzi) (subset with rz_balance>0)
        #   margin_balance = SUM(rz_balance)
        #   margin_buy = SUM(rz_buy)
        #   margin_weight_share = SUM(__weight_x_rongzi) (parent_index_weight
        #     for the actively-traded subset only)
        agg = merged.groupby(["date", "industry_id", "industry_label"], as_index=False).agg(
            **{
                f"{prefix}_count": ("code", "count"),
                f"{prefix}_margin_count": ("has_rongzi", "sum"),
                f"{prefix}_margin_balance": ("rz_balance", "sum"),
                f"{prefix}_margin_buy": ("rz_buy", "sum"),
                f"{prefix}_margin_weight_share": ("__weight_x_rongzi", "sum"),
            }
        )
        out_frames.append(agg)

    if not out_frames:
        return pd.DataFrame(
            columns=[
                "date", "industry_id", "industry_label",
                "stock_count", "stock_margin_count",
                "stock_margin_weight_share",
                "stock_margin_balance",
                "etf_count", "etf_margin_count",
                "etf_margin_balance",
                "stock_margin_buy", "etf_margin_buy",
            ]
        )

    # Outer-join the stock and etf aggregations on (date, industry_id).
    # An industry might have stocks but no ETFs (or vice versa) on a given
    # date — fill missing side with 0 for counts/balances, NULL for
    # weight_share (only stocks have weights).
    stock_agg = next(
        (f for f in out_frames if "stock_count" in f.columns), None
    )
    etf_agg = next(
        (f for f in out_frames if "etf_count" in f.columns), None
    )

    if stock_agg is not None and etf_agg is not None:
        merged = stock_agg.merge(
            etf_agg,
            on=["date", "industry_id", "industry_label"],
            how="outer",
        )
    elif stock_agg is not None:
        merged = stock_agg
        # Add empty etf columns.
        merged["etf_count"] = 0
        merged["etf_margin_count"] = 0
        merged["etf_margin_balance"] = 0.0
        merged["etf_margin_buy"] = 0.0
    else:
        merged = etf_agg  # type: ignore[assignment]
        merged["stock_count"] = 0
        merged["stock_margin_count"] = 0
        merged["stock_margin_weight_share"] = None
        merged["stock_margin_balance"] = 0.0
        merged["stock_margin_buy"] = 0.0

    # Fill NaN counts/balances with 0 (outer join produces NaN where one
    # side is missing). weight_share stays NaN for the etf side (NULL in
    # DB). industry_label may be NaN if both sides miss — fillna('').
    fill_zero = [
        "stock_count", "stock_margin_count",
        "stock_margin_balance", "stock_margin_buy",
        "etf_count", "etf_margin_count",
        "etf_margin_balance", "etf_margin_buy",
    ]
    for c in fill_zero:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0)
    if "stock_margin_weight_share" in merged.columns:
        # Keep NaN for etf-only industries (NULL in DB); fill 0 only where
        # stock_count > 0 but weight_share is NaN (e.g. parent_index_weight
        # was NULL in source).
        mask = (merged["stock_count"] > 0) & (
            merged["stock_margin_weight_share"].isna()
        )
        merged.loc[mask, "stock_margin_weight_share"] = 0
    merged["industry_label"] = merged["industry_label"].fillna("")

    # Cast integer counts back from float (groupby.agg sometimes returns
    # float64 for count columns when an outer join is involved).
    for c in ["stock_count", "stock_margin_count",
              "etf_count", "etf_margin_count"]:
        if c in merged.columns:
            merged[c] = merged[c].astype(int)

    # Reorder columns to match DB schema (excluding GENERATED columns).
    column_order = [
        "date", "industry_id", "industry_label",
        "stock_count", "stock_margin_count", "stock_margin_weight_share",
        "etf_count", "etf_margin_count",
        "stock_margin_balance", "etf_margin_balance",
        "stock_margin_buy", "etf_margin_buy",
    ]
    return merged[column_order].sort_values(
        ["date", "industry_id"]
    ).reset_index(drop=True)
