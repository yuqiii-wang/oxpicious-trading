"""Options wall levels per expiry group (analysis.options_walls).

Two wall types:
  80pct    — contiguous zone from one end of the chain where one side
             dominates >= 80% of total OI at each strike, interpolated
             boundary at the threshold.
  large_num — strike with the max OI among those exceeding 70% of the
             mean OI across all strikes in the expiry group.

Fully vectorized (B-A4 refactor): the former per-expiry-group Python
loop (2 inner groupbys + dict(zip) + sorted(set) per group — the 47k
RuntimeError storm under cudf.pandas) is replaced by wide
groupby-transform passes over the whole frame:

  - one CALL/PUT outer-merge on (key, strike) builds the union frame
    with per-strike call/put OI and put_pct;
  - the 80pct walls use "min/max row-number among threshold-breaking
    rows" via groupby-transform (NaN = whole chain qualifies), then
    ONE self-merge per boundary row for the interpolated strike;
  - the large_num walls use a groupby-transform mean + stable
    descending sort + drop_duplicates (first-max = lowest strike on
    ties, matching the former dict-iteration order).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.options.compute._shared import _apply_open_expiry_collapse


_WALLS_GROUP_KEY = ["date", "underlying_code", "expiry_date"]


def _boundary_rows(
    union: pd.DataFrame,
    anchors: pd.DataFrame,
    rn_col: str,
    value_cols: list[str],
) -> pd.DataFrame:
    """Attach the union rows at per-group row numbers ``rn_col``.

    One left-merge per call: ``anchors`` carries the group key columns
    plus ``rn_col`` (a row number within the group, as produced by
    cumcount on the strike-sorted union frame); the returned frame adds
    each column of ``value_cols`` from the matching union row.
    """
    look = union[_WALLS_GROUP_KEY + ["_rn"] + value_cols]
    return anchors.merge(
        look,
        left_on=_WALLS_GROUP_KEY + [rn_col],
        right_on=_WALLS_GROUP_KEY + ["_rn"],
        how="left",
    )


def _large_num_walls(
    side: pd.DataFrame,
    oi_col: str,
) -> pd.DataFrame:
    """Large-num wall per group for one option type's strike frame.

    mean OI across the side's own strikes (unweighted — matches the
    former per-group dict mean); qualifying strikes have OI >= 70% of
    the mean; the winner is the max-OI strike, ties broken to the
    LOWEST strike (stable mergesort keeps the strike order among equal
    OI values — identical to the former dict-iteration ``>`` scan).
    """
    from analyze.options.config import LARGE_NUM_MEAN_FRACTION

    if side.empty:
        return pd.DataFrame(
            columns=_WALLS_GROUP_KEY
            + ["wall_strike_raw", "wall_oi", "mean_oi"]
        )
    s = side.sort_values(_WALLS_GROUP_KEY + ["strike_price"]).reset_index(
        drop=True
    )
    s["_mean_oi"] = s.groupby(_WALLS_GROUP_KEY, sort=False)[
        oi_col
    ].transform("mean")
    qual = s[s[oi_col] >= s["_mean_oi"] * LARGE_NUM_MEAN_FRACTION]
    if qual.empty:
        return pd.DataFrame(
            columns=_WALLS_GROUP_KEY
            + ["wall_strike_raw", "wall_oi", "mean_oi"]
        )
    best = (
        qual.assign(_neg_oi=-qual[oi_col].astype("float64"))
        .sort_values(["_neg_oi", "strike_price"])
        .drop_duplicates(subset=_WALLS_GROUP_KEY, keep="first")
    )
    best = best[_WALLS_GROUP_KEY + ["strike_price", oi_col, "_mean_oi"]].rename(
        columns={
            "strike_price": "wall_strike_raw",
            oi_col: "wall_oi",
            "_mean_oi": "mean_oi",
        }
    )
    return best


def compute_options_walls(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group options wall levels for both 80pct and
    large_num wall types.

    For each (date, underlying_code, expiry_date), aggregates OI by strike
    separately for CALL and PUT, then computes:

    80pct wall:
      - Bear (PUT): contiguous zone from lowest strike where putPct >= 80%,
        linearly interpolated boundary at the 80% threshold.
      - Bull (CALL): contiguous zone from highest strike where putPct <= 20%,
        linearly interpolated boundary at the 20% threshold.

    large_num wall:
      - For each option type, compute mean OI across all strikes.
      - Filter strikes where OI > 70% of the mean value.
      - Pick the strike with the maximum OI among qualifying strikes.

    Args:
        df: DataFrame with columns:
            date, contract_code, option_type, underlying_code, expiry_date,
            strike_price, open_interest.

    Returns:
        DataFrame with WALLS_RESULT_COLUMNS — one row per
        (date, option_type, underlying_code, expiry_date, wall_type).
    """
    from analyze.options.config import (
        WALLS_RESULT_COLUMNS,
        WALL_TYPE_80PCT,
        WALL_TYPE_LARGE_NUM,
        PUT_PCT_RED,
        PUT_PCT_GREEN,
        PRICE_SCALE,
        LARGE_NUM_MEAN_FRACTION,
    )

    empty = pd.DataFrame(columns=WALLS_RESULT_COLUMNS)
    if df.empty:
        return empty

    # Open-expiry collapse (vectorized shared helper; uses the same
    # dataset max date the former inline collapse computed).
    data = _apply_open_expiry_collapse(df.copy())
    if data.empty:
        return empty

    # Aggregate OI by (date, underlying, expiry, option_type, strike)
    agg = (
        data.groupby(
            ["date", "underlying_code", "expiry_date", "option_type",
             "strike_price"],
            as_index=False,
            sort=False,
        )
        .agg(open_interest=("open_interest", "sum"))
    )

    # Per-side strike frames (a strike belongs to a side's mean only if
    # that side actually has a row there — the former per-group dicts
    # contained only real rows).
    call_side = agg.loc[
        agg["option_type"] == "CALL",
        _WALLS_GROUP_KEY + ["strike_price", "open_interest"],
    ].rename(columns={"open_interest": "call_oi"})
    put_side = agg.loc[
        agg["option_type"] == "PUT",
        _WALLS_GROUP_KEY + ["strike_price", "open_interest"],
    ].rename(columns={"open_interest": "put_oi"})

    # Union of strikes per group (put_pct / 80pct runs operate on the
    # union; strikes with zero total OI are dropped — the former
    # strike_data filter).
    union = call_side.merge(
        put_side, on=_WALLS_GROUP_KEY + ["strike_price"], how="outer",
    )
    if union.empty:
        return empty
    union["call_oi"] = union["call_oi"].fillna(0.0)
    union["put_oi"] = union["put_oi"].fillna(0.0)
    total = union["call_oi"] + union["put_oi"]
    union = union[total > 0].copy()
    if union.empty:
        return empty
    union = union.sort_values(
        _WALLS_GROUP_KEY + ["strike_price"]
    ).reset_index(drop=True)
    union["put_pct"] = (
        union["put_oi"] / (union["call_oi"] + union["put_oi"]) * 100.0
    )
    union["_rn"] = union.groupby(_WALLS_GROUP_KEY, sort=False).cumcount()

    gkeys = [union[c] for c in _WALLS_GROUP_KEY]
    last_rn = union["_rn"].groupby(gkeys, sort=False).transform("max")

    # ---- 80pct walls ---------------------------------------------------
    # Bear (PUT): contiguous run from the LOWEST strike while
    # put_pct >= PUT_PCT_RED; the run breaks at the first bad row.
    bear_bad = union["put_pct"] < PUT_PCT_RED
    first_bad = (
        union["_rn"].where(bear_bad)
        .groupby(gkeys, sort=False).transform("min")
    )
    # Bull (CALL): contiguous run from the HIGHEST strike while
    # put_pct <= PUT_PCT_GREEN; the run breaks at the last bad row.
    bull_bad = union["put_pct"] > PUT_PCT_GREEN
    last_bad = (
        union["_rn"].where(bull_bad)
        .groupby(gkeys, sort=False).transform("max")
    )

    wall_frames: list[pd.DataFrame] = []

    # Bear interpolated: first_bad in (0, last_rn] — boundary rows
    # a = first_bad-1 (put_pct >= threshold), b = first_bad (< threshold).
    # Bull interpolated: last_bad in [0, last_rn) — boundary rows
    # a = last_bad (put_pct > threshold), b = last_bad+1 (<= threshold).
    # anchors must be ONE row per GROUP (first_bad/last_bad are group-level
    # broadcast values; collapsing via drop_duplicates on key + value).
    def _anchors_from_bad(bad_rn: pd.Series, b_offset: int) -> pd.DataFrame:
        rows = pd.DataFrame({
            "date": union["date"],
            "underlying_code": union["underlying_code"],
            "expiry_date": union["expiry_date"],
            "_fb": bad_rn,
        }).drop_duplicates()
        rows = rows[rows["_fb"].notna()].copy()
        rows["_rn_b"] = rows.pop("_fb").astype("int64") + b_offset
        rows["_rn_a"] = rows["_rn_b"] - 1
        return rows

    bear_int_mask = first_bad.notna() & (first_bad > 0)
    if bear_int_mask.any():
        anchors = _anchors_from_bad(first_bad.where(bear_int_mask), 0)
        a = _boundary_rows(
            union, anchors, "_rn_a",
            ["strike_price", "put_pct", "put_oi"],
        ).rename(columns={
            "strike_price": "_a_strike", "put_pct": "_a_pct",
            "put_oi": "_a_oi",
        })
        b = _boundary_rows(
            union, a, "_rn_b",
            ["strike_price", "put_pct", "put_oi"],
        ).rename(columns={
            "strike_price": "_b_strike", "put_pct": "_b_pct",
            "put_oi": "_b_oi",
        })
        d_pct = b["_b_pct"].to_numpy(dtype=np.float64) - \
            b["_a_pct"].to_numpy(dtype=np.float64)
        a_strike = b["_a_strike"].to_numpy(dtype=np.float64)
        b_strike = b["_b_strike"].to_numpy(dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(
                d_pct == 0, 0.0, (PUT_PCT_RED - b["_a_pct"].to_numpy(
                    dtype=np.float64)) / d_pct
            )
        wall = np.where(
            d_pct == 0, b_strike, a_strike + frac * (b_strike - a_strike)
        )
        # wall_oi = put OI at the wall strike when it coincides with a
        # real boundary strike (frac == 0 / d_pct == 0), else 0 — the
        # former put_oi.get(strike, 0) semantics.
        oi = np.where(
            wall == a_strike, b["_a_oi"].to_numpy(dtype=np.float64),
            np.where(wall == b_strike, b["_b_oi"].to_numpy(dtype=np.float64),
                     0.0),
        )
        wall_frames.append(pd.DataFrame({
            "date": b["date"],
            "option_type": "PUT",
            "underlying_code": b["underlying_code"],
            "expiry_date": b["expiry_date"],
            "wall_type": WALL_TYPE_80PCT,
            "wall_strike_raw": wall,
            "wall_oi": oi,
            "mean_oi": np.nan,
            "threshold": PUT_PCT_RED / 100.0,
        }))

    # Bear full chain: no bad row — the whole chain is put-dominant;
    # wall = highest strike of the group.
    bear_full_mask = first_bad.isna()
    if bear_full_mask.any():
        rows = union.loc[
            bear_full_mask & (union["_rn"] == last_rn),
            _WALLS_GROUP_KEY + ["strike_price", "put_oi"],
        ]
        wall_frames.append(pd.DataFrame({
            "date": rows["date"],
            "option_type": "PUT",
            "underlying_code": rows["underlying_code"],
            "expiry_date": rows["expiry_date"],
            "wall_type": WALL_TYPE_80PCT,
            "wall_strike_raw": rows["strike_price"].to_numpy(
                dtype=np.float64),
            "wall_oi": rows["put_oi"].to_numpy(dtype=np.float64),
            "mean_oi": np.nan,
            "threshold": PUT_PCT_RED / 100.0,
        }))

    # Bull interpolated: last_bad in [0, last_rn) — boundary rows
    # a = last_bad (put_pct > threshold), b = last_bad+1 (<= threshold).
    bull_int_mask = last_bad.notna() & (last_bad < last_rn)
    if bull_int_mask.any():
        anchors = _anchors_from_bad(last_bad.where(bull_int_mask), 1)
        a = _boundary_rows(
            union, anchors, "_rn_a",
            ["strike_price", "put_pct", "call_oi"],
        ).rename(columns={
            "strike_price": "_a_strike", "put_pct": "_a_pct",
            "call_oi": "_a_oi",
        })
        b = _boundary_rows(
            union, a, "_rn_b",
            ["strike_price", "put_pct", "call_oi"],
        ).rename(columns={
            "strike_price": "_b_strike", "put_pct": "_b_pct",
            "call_oi": "_b_oi",
        })
        d_pct = b["_b_pct"].to_numpy(dtype=np.float64) - \
            b["_a_pct"].to_numpy(dtype=np.float64)
        a_strike = b["_a_strike"].to_numpy(dtype=np.float64)
        b_strike = b["_b_strike"].to_numpy(dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(
                d_pct == 0, 0.0, (PUT_PCT_GREEN - b["_a_pct"].to_numpy(
                    dtype=np.float64)) / d_pct
            )
        wall = np.where(
            d_pct == 0, b_strike, a_strike + frac * (b_strike - a_strike)
        )
        oi = np.where(
            wall == a_strike, b["_a_oi"].to_numpy(dtype=np.float64),
            np.where(wall == b_strike, b["_b_oi"].to_numpy(dtype=np.float64),
                     0.0),
        )
        wall_frames.append(pd.DataFrame({
            "date": b["date"],
            "option_type": "CALL",
            "underlying_code": b["underlying_code"],
            "expiry_date": b["expiry_date"],
            "wall_type": WALL_TYPE_80PCT,
            "wall_strike_raw": wall,
            "wall_oi": oi,
            "mean_oi": np.nan,
            "threshold": PUT_PCT_GREEN / 100.0,
        }))

    # Bull full chain: no bad row — call-dominant throughout;
    # wall = lowest strike of the group.
    bull_full_mask = last_bad.isna()
    if bull_full_mask.any():
        rows = union.loc[
            bull_full_mask & (union["_rn"] == 0),
            _WALLS_GROUP_KEY + ["strike_price", "call_oi"],
        ]
        wall_frames.append(pd.DataFrame({
            "date": rows["date"],
            "option_type": "CALL",
            "underlying_code": rows["underlying_code"],
            "expiry_date": rows["expiry_date"],
            "wall_type": WALL_TYPE_80PCT,
            "wall_strike_raw": rows["strike_price"].to_numpy(
                dtype=np.float64),
            "wall_oi": rows["call_oi"].to_numpy(dtype=np.float64),
            "mean_oi": np.nan,
            "threshold": PUT_PCT_GREEN / 100.0,
        }))

    # ---- large_num walls ------------------------------------------------
    for side_df, oi_col, ot in (
        (call_side, "call_oi", "CALL"),
        (put_side, "put_oi", "PUT"),
    ):
        best = _large_num_walls(side_df, oi_col)
        if best.empty:
            continue
        wall_frames.append(pd.DataFrame({
            "date": best["date"],
            "option_type": ot,
            "underlying_code": best["underlying_code"],
            "expiry_date": best["expiry_date"],
            "wall_type": WALL_TYPE_LARGE_NUM,
            "wall_strike_raw": best["wall_strike_raw"].to_numpy(
                dtype=np.float64),
            "wall_oi": best["wall_oi"].to_numpy(dtype=np.float64),
            "mean_oi": best["mean_oi"].to_numpy(dtype=np.float64),
            "threshold": LARGE_NUM_MEAN_FRACTION,
        }))

    if not wall_frames:
        return empty

    result = pd.concat(wall_frames, ignore_index=True)
    result["wall_strike"] = result["wall_strike_raw"] / PRICE_SCALE
    result = result[WALLS_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date", "wall_type"]
    ).reset_index(drop=True)

    return result
