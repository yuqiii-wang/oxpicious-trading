"""Pandas transformations for analysis.margins.

Three compute functions (all REDUCED / moved in the margin cleanup):

  - ``compute_tech_stats``: per-(sec_type, code) regime-detection columns
    on rz_balance (slope_ma5 + zscore_20d) consumed by the margin_changes
    trend episode detection, producing rows for analysis.margin_tech_stats.

  - ``compute_industry_stats``: per-(date, industry_id) aggregation of
    rz_balance / rz_buy across stocks and ETFs in each industry — a SUM
    per side (raw member totals) PLUS a *_norm AVERAGE per side over
    members with a non-null non-zero value (the per-member-normalized
    curve behind the Margin Trends raw-vs-normalized toggle), producing
    rows for analysis.margin_industry_stats.

  - ``compute_index_margin_series``: per-(index_code, date) weighted-
    AVERAGE rongzi margin series (branch 1 stock-based + branch 2
    ETF-proxy), producing rows for the analysis.margin_index_series TABLE
    (the aggregation was moved out of SQL into this pandas vectorization).

SECTIONING (2026-10-19): the rz_balance curve is SECTIONED before any
rise/drop attribution — a section is a maximal run of consecutive rows
with a valid (>0) balance AND an unchanged contributing population. A
margin rise/drop is never measured ACROSS a section boundary: the daily
slope, its MA5/MA20/STD20 windows, and the margin_changes trend episodes
all reset at boundaries. Two boundary kinds (both rendered as dots /
line-breaks on the Margin Trends page):

  - null gaps — the first valid row after a 0/NULL rz_balance run (the
    plotted line breaks; the balance may have moved arbitrarily inside
    the gap, so the cross-gap delta is not a one-day move);
  - membership changes — industry rows where the contributing-member
    count (Σ per-side SUM/norm) differs from the previous row, and index
    rows where n_with_balance differs (a new member's balance jolts the
    SUM / re-weights the average for population reasons, not margin
    behavior; study 2026-10-19: 7.4% of industry episodes spanned such a
    day with a median 13% structural share of the move, worst cases 99%).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, host_unique, safe_columns
from _common.labels import SEC_TYPE_CODE


# ---- Regime-detection windows ---------------------------------------------
# slope_ma5 (5d rolling mean of the daily balance slope) is the
# segmentation signal; slope_ma20 + slope_std20 are INTERNAL intermediates
# for the zscore. Only slope_ma5 + zscore_20d are persisted.
SLOPE_MA_WINDOWS = [5, 20]
SLOPE_STD_WINDOW = 20

# Passenger column carrying the contributing-population count per row
# (industry: contributing members behind the SUM; index: constituents
# behind the weighted average). Absent for etf/stock per-security curves.
MEMBER_COUNT_COL = "n_members"


# ---------------------------------------------------------------------------
#  Section flags — shared by compute_tech_stats + episode detection
# ---------------------------------------------------------------------------

def attach_section_flags(df: pd.DataFrame,
                         member_col: str | None = None) -> None:
    """Mark section boundaries on a margin curve frame (in place).

    Adds ``__section_break`` (bool — True on the FIRST row of a new
    section) and ``__section_id`` (int64 — 0-based within each code;
    rows between sections keep the pre-boundary id). The sectioning is
    the rise/drop-attribution contract: no slope, rolling window, or
    trend episode may span a boundary.

    Boundary rules (a boundary row is always a VALID-balance row):
      - the first valid (>0) row of a code, and the first valid row after
        any 0/NULL rz_balance run (null-gap boundary);
      - when ``member_col`` is given: a valid row whose member count
        differs from the previous row of the same code (membership-change
        boundary — population shift behind an aggregate curve).

    Args:
        df: DataFrame with [code, date, rz_balance], ALREADY sorted by
            (code, date). Modified in place (two columns added).
        member_col: optional population-count column (see MEMBER_COUNT_COL).
            NaN counts never mark a boundary (unknown ≠ changed).
    """
    valid = (
        pd.to_numeric(df["rz_balance"], errors="coerce") > 0
    ).fillna(False)
    df["__valid_f"] = valid.astype("float64")
    prev_valid = df.groupby(
        "code", sort=False
    )["__valid_f"].shift(1).fillna(0.0)
    # after_gap: valid today, previous row of the code not valid (also
    # True on the code's first row — its own slope is NULL anyway).
    after_gap = (df["__valid_f"] > 0.5) & (prev_valid < 0.5)
    boundary = after_gap

    if member_col is not None and member_col in safe_columns(df):
        members = pd.to_numeric(df[member_col], errors="coerce")
        df["__prev_members_f"] = members.astype("float64")
        prev_members = df.groupby(
            "code", sort=False
        )["__prev_members_f"].shift(1)
        member_changed = (
            (df["__valid_f"] > 0.5)
            & prev_members.notna()
            & (df["__prev_members_f"] != prev_members).fillna(False)
        )
        boundary = after_gap | member_changed
        df.drop(columns=["__prev_members_f"], inplace=True)

    df["__section_break"] = boundary
    df["__section_break_i"] = boundary.astype("int64")
    df["__section_id"] = df.groupby(
        "code", sort=False
    )["__section_break_i"].cumsum().astype("int64")
    df.drop(columns=["__valid_f", "__section_break_i"], inplace=True)


# ---------------------------------------------------------------------------
#  Per-(sec_type, code) regime-detection columns
# ---------------------------------------------------------------------------

def compute_tech_stats(df: pd.DataFrame, sec_type: str) -> pd.DataFrame:
    """Compute the margin_balance regime-detection cols per code.

    Input:
      df with columns [code, date, rz_balance, rz_buy] sorted by (code,
      date), plus the optional ``n_members`` population passenger
      (industry / index curves — see attach_section_flags).

    Output:
      DataFrame with columns:
        sec_type, code, date,
        margin_balance_slope_ma5, margin_balance_slope_zscore_20d
      Sorted by (sec_type, code, date). One row per (code, date).

    Conventions:
      - INVALID-VALUE RULE (rz_balance == 0 OR NULL → NaN): a 0 rz_balance
        means "no rongzi position" — missing data for rolling purposes.
      - SECTIONING (see the module docstring): the slope and every
        rolling window are computed WITHIN sections — null-gap and
        membership-change boundaries (``__section_break``). The slope is
        NULL on a boundary row (no cross-boundary delta); the MA/STD
        windows never mix slopes from different sections.
      - slope: (X[t] - X[t-1]) / X[t-1], NULL on the first date of each
        SECTION or when X[t-1] <= 0 (denominator guard).
      - slope_ma5: rolling(5, min_periods=1).mean() of the daily slope
        within the section.
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

    # ---- Section flags (null-gap + membership boundaries) ------------
    attach_section_flags(work, member_col=MEMBER_COUNT_COL)
    grp_keys = ["code", "__section_id"]

    # ---- Invalid-value cleaning: 0 / NULL → NaN ----------------------
    cleaned = pd.to_numeric(work["rz_balance"], errors="coerce")
    work["rz_balance"] = cleaned.where(cleaned > 0)

    # ---- slope = (X[t] - X[t-1]) / X[t-1] (within section) -----------
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
            "sec_type": SEC_TYPE_CODE[sec_type],
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

_INDUSTRY_STATS_COLUMNS = [
    "date", "industry_id", "industry_label",
    "stock_margin_balance", "etf_margin_balance",
    "stock_margin_buy", "etf_margin_buy",
    "stock_margin_balance_norm", "etf_margin_balance_norm",
    "stock_margin_buy_norm", "etf_margin_buy_norm",
]


def _industry_code_frame(
    etf_industry_map: pd.DataFrame,
    stock_industry_map: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """One shared factorization of the run's industry ids (the
    label-code contract): industry_id/industry_label strings never ride
    the 7M-row merge/groupby/outer-join chain — the compute keys on the
    int32 ``industry_code`` and the small frame re-attaches the TEXT
    identity at the write boundary. Returns (code_frame, label->code).
    """
    both = pd.concat([
        m[["industry_id", "industry_label"]]
        for m in (stock_industry_map, etf_industry_map) if not m.empty
    ], ignore_index=True)
    if both.empty:
        return (
            pd.DataFrame(
                columns=["industry_code", "industry_id", "industry_label"]
            ),
            {},
        )
    labels = host_unique(both["industry_id"])
    code_of = {u: i for i, u in enumerate(labels)}
    frame = pd.DataFrame({
        "industry_code": np.arange(len(labels), dtype="int32"),
        "industry_id": labels,
    }).merge(
        both.drop_duplicates(subset=["industry_id"]),
        on="industry_id", how="left",
    )
    return frame, code_of


def compute_industry_stats(
    etf_tech: pd.DataFrame,
    stock_tech: pd.DataFrame,
    etf_industry_map: pd.DataFrame,
    stock_industry_map: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate per-(sec_type, code, date) rz_balance / rz_buy into
    per-(date, industry_id) side stats split by stock / etf: the SUM of
    member flows (raw columns) plus the AVERAGE over members with a
    NON-NULL NON-ZERO value (*_norm columns — the per-member-normalized
    curve; zero-margin members are excluded from BOTH numerator and
    denominator, so a day's norm reflects only contributing members).

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
      etf_margin_balance, stock_margin_buy, etf_margin_buy,
      stock_margin_balance_norm, etf_margin_balance_norm,
      stock_margin_buy_norm, etf_margin_buy_norm].
      One row per (date, industry_id).

    LABEL-CODE CONTRACT: the merge/groupby/outer-join/sort chain runs on
    the int32 industry_code (one shared factorization of the run's
    industry ids); industry_id/industry_label TEXT values are re-attached
    by the final merge onto the small _industry_code_frame — the string
    columns never ride the big frames (the cuDF 26.08 string
    host-transfer fault class).
    """
    out_frames: list[pd.DataFrame] = []
    code_frame, code_of = _industry_code_frame(
        etf_industry_map, stock_industry_map,
    )

    for hist_df, map_df, prefix in [
        (stock_tech, stock_industry_map, "stock"),
        (etf_tech, etf_industry_map, "etf"),
    ]:
        if hist_df.empty or map_df.empty:
            continue

        # Join history with the industry CODE (label-code contract —
        # int keys through the whole chain) — drops codes with no
        # industry (e.g. ETFs tracking BROAD indices).
        map_codes = map_df[["code", "industry_id"]].copy()
        map_codes["industry_code"] = (
            map_codes["industry_id"].map(code_of).astype("int32")
        )
        merged = hist_df.merge(
            map_codes[["code", "industry_code"]],
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
        # NORM inputs: values <= 0 → NaN so the grouped MEAN skips them
        # (average of the non-null non-zero members only; an all-invalid
        # group means to NaN → NULL, matching the SUM's zero→NULL rule).
        merged["__balance_pos"] = merged["rz_balance"].where(
            merged["rz_balance"] > 0
        )
        merged["__buy_pos"] = merged["rz_buy"].where(merged["rz_buy"] > 0)

        agg = merged.groupby(
            ["date", "industry_code"], as_index=False
        ).agg(
            **{
                f"{prefix}_margin_balance": ("rz_balance", "sum"),
                f"{prefix}_margin_buy": ("rz_buy", "sum"),
                f"{prefix}_margin_balance_norm": ("__balance_pos", "mean"),
                f"{prefix}_margin_buy_norm": ("__buy_pos", "mean"),
            }
        )
        # ZERO-SUM -> NULL: a 0 SUM means NO member contributed on that
        # date (missing data), not a genuine zero-margin day — store NULL
        # so the UI series breaks the line instead of dipping to 0. Also
        # normalizes the cudf all-null-group SUM (null) vs pandas (0.0).
        # Applied to the norms too for symmetry (a positive-only mean
        # cannot legitimately be 0).
        for c in (
            f"{prefix}_margin_balance", f"{prefix}_margin_buy",
            f"{prefix}_margin_balance_norm", f"{prefix}_margin_buy_norm",
        ):
            agg[c] = agg[c].where(agg[c] > 0)
        out_frames.append(agg)

    if not out_frames:
        return pd.DataFrame(columns=_INDUSTRY_STATS_COLUMNS)

    # Outer-join the stock and etf aggregations on (date, industry_code).
    # An industry might have stocks but no ETFs (or vice versa) on a given
    # date — fill missing side with 0.
    # Host-pure membership via safe_columns — `in f.columns` is a proxied
    # Index.__contains__ that falls back ("transfer blocking") under
    # cudf.pandas.
    stock_agg = next(
        (f for f in out_frames
         if "stock_margin_balance" in safe_columns(f)), None
    )
    etf_agg = next(
        (f for f in out_frames
         if "etf_margin_balance" in safe_columns(f)), None
    )

    if stock_agg is not None and etf_agg is not None:
        merged = stock_agg.merge(
            etf_agg,
            on=["date", "industry_code"],
            how="outer",
        )
    elif stock_agg is not None:
        merged = stock_agg
        for c in ("etf_margin_balance", "etf_margin_buy",
                  "etf_margin_balance_norm", "etf_margin_buy_norm"):
            merged[c] = np.nan
    else:
        merged = etf_agg  # type: ignore[assignment]
        for c in ("stock_margin_balance", "stock_margin_buy",
                  "stock_margin_balance_norm", "stock_margin_buy_norm"):
            merged[c] = np.nan

    # NO fillna(0): the outer join leaves NaN where one side is missing —
    # that IS the semantics (no contribution on that date -> NULL in DB).

    # Write boundary: re-attach the TEXT identity (industry_id /
    # industry_label) from the small code frame — the labels never rode
    # the big chain. industry_label may still be NULL for an id without
    # a label row — fillna('').
    merged = merged.merge(code_frame, on="industry_code", how="left")
    merged["industry_label"] = merged["industry_label"].fillna("")

    return (
        merged.sort_values(["date", "industry_code"])[_INDUSTRY_STATS_COLUMNS]
        .reset_index(drop=True)
    )


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
