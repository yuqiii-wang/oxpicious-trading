"""Rolling 5-year dividend DELIVERY audit — the ``deliveries`` stage of
analyze.pe_and_dividends (2026-10-03, etf + stock scope; index has no
per-share deliveries and skips).

MODEL (per code): one row per rolling window of
DELIVERIES_WINDOW_YEARS calendar years, strided by 1 year. The window
anchors are each calendar year's LAST data date (per-code max close
date; the current year anchors at the latest close — the open, mutable
window). window = [anchor - 5y + 1 day, anchor] (Feb-29 anchors clamp
to Feb-28 five years back).

A DELIVERY is one ex-dividend event with a strictly positive per-share
cash dividend. Per event:
  yield  = D / close(ex-date)  (raw close; NaN when the ex-date has no
                                close row — suspended stock)
  eps    = stats.*_basic_stats.eps at the ex-date (NULL when pe is NULL)
  stable = 1 when the same code also delivered within +/-
           DELIVERIES_STABILITY_TOL_DAYS days of the same date ONE YEAR
           earlier (365d +/- tol repeat window; leap-year drift of +/-
           1d sits inside the tolerance); a series' first-ever delivery
           is never stable.

Window aggregates: deliveries count, distinct event years, mean
per-event yield, annualized yield (sum of per-event yields / window
span years — every window spans anchor-5y+1d..anchor), latest-year
yield (sum of per-event yields over the final 365d of the window —
the trailing-1y yield at the anchor), mean per-event eps, stable
count, stability = stable_events / deliveries. Rows only for PAYER
codes (>= 1 event in history) and only for anchors at/after the
code's first delivery; zero-delivery windows in between are stored
with 0 counts and NULL aggregates (an audit must show the gap).

Incremental contract: the table is tiny (payers x annual windows), and
the current-year anchor + late-arriving events are mutable — the stage
rebuilds the sec_type's scope WHOLESALE on every run that processes it
(force = purge + COPY; otherwise full upsert — the pe_pct_streaks
precedent).

COMPUTE LAYOUT (cudf GPU-first): one whole-frame pass — per-event
metrics via merge + elementwise columns, the stability flag via a
cross-expand of the event days over the +/- tolerance offsets joined
on the deduplicated (code, epoch-day) event-key grid, anchors via
groupby max, and the window aggregation via ONE per-code anchors x
events cross-merge filtered to the window bounds + grouped single-agg
series concat (the tech_stats _tail_rate_table pattern). Calendar-year
arithmetic (anchor - 5y) runs as elementwise int64 column ops
(days_from_civil); no python dates anywhere upstream of
sanitize_for_db_insert.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from _common.db_commons import (
    analyze_tables_async,
    chunked_purge_async,
    copy_frame_chunked_async,
    copy_or_upsert_split_async,
)
from _common.df_utils.sanitize import safe_columns

from analyze._common.sanitize import sanitize_for_db_insert
from analyze.pe_and_dividends.dividends.config import (
    DELIVERIES_STABILITY_TOL_DAYS,
    DELIVERIES_TABLE,
    DELIVERIES_WINDOW_YEARS,
)

logger = logging.getLogger("pe_and_dividends")

_US_PER_DAY = 86_400_000_000  # datetime64[us] ticks per calendar day

_OUTPUT_COLUMNS = [
    "code", "window_end_date", "window_start_date",
    "deliveries", "years_with_delivery",
    "avg_yield", "annualized_yield", "latest_year_yield", "avg_eps",
    "stable_events", "stability",
]

_WRITE_COLUMNS = ["sec_type", *_OUTPUT_COLUMNS]
_WRITE_NUMERIC_COLS = [
    "deliveries", "years_with_delivery", "avg_yield", "annualized_yield",
    "latest_year_yield", "avg_eps", "stable_events", "stability",
]
_WRITE_DATE_COLS = ["window_end_date", "window_start_date"]


# ---------------------------------------------------------------------------
#  Calendar-year arithmetic (vectorized, int64 frame columns)
# ---------------------------------------------------------------------------
def _ymd_to_epoch_day(y, m, d):
    """(year, month, day) int64 frame columns -> days-since-epoch column.

    Howard Hinnant's days_from_civil as elementwise column ops (Series.where
    + int arithmetic — cuDF-native; no python dates inside the frame).
    """
    y2 = y.where(m > 2, y - 1)
    mp = (m - 3).where(m > 2, m + 9)
    era = y2 // 400  # python floor division == the era formula
    yoe = y2 - era * 400
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


# ---------------------------------------------------------------------------
#  Compute
# ---------------------------------------------------------------------------
def _prepare_events(close_df: pd.DataFrame, events_df: pd.DataFrame) -> pd.DataFrame:
    """Attach per-event yield / eps / stability flags to the delivery
    events (whole-frame: merge + elementwise columns; events whose ex-date
    has no close row keep NaN yield/eps but still count as deliveries)."""
    ev = events_df.rename(columns={
        "ex_dividend_date": "date",
        "dividend_per_share_pre_tax": "div",
    })[["code", "date", "div"]]
    close_cols = ["code", "date", "close"]
    if "eps" in safe_columns(close_df):
        close_cols.append("eps")
    ev = ev.merge(close_df[close_cols], on=["code", "date"], how="left")
    if "eps" not in safe_columns(ev):
        ev["eps"] = np.nan
    ev["yield"] = ev["div"] / ev["close"].where(ev["close"] > 0)
    ev["_y"] = ev["date"].dt.year.astype("int64")
    ev["_dayi"] = (
        ev["date"].astype("int64") // np.int64(_US_PER_DAY)
    ).astype("int64")

    # Stability (last-year +/- tol repeat): the event at epoch-day d is
    # stable when the code also delivered within [d - 365 - tol,
    # d - 365 + tol] — the same date one year earlier, +/- the
    # tolerance. Whole-frame formulation: every event day is
    # cross-expanded over the 2*tol+1 integer offsets (constant-key
    # merge with the tiny offsets frame), the candidates are
    # left-joined on the DEDUPLICATED (code, day) event grid (so left
    # rows never multiply), and a grouped count per (code, day) flags
    # days with at least one matched offset.
    offs = pd.DataFrame({
        "_off": np.arange(
            -DELIVERIES_STABILITY_TOL_DAYS, DELIVERIES_STABILITY_TOL_DAYS + 1,
            dtype="int64",
        ),
    })
    offs["_k"] = np.int64(1)
    cand = ev[["code", "_dayi"]].copy()
    cand["_k"] = np.int64(1)
    cand = cand.merge(offs, on="_k")
    cand["_cd"] = cand["_dayi"] - np.int64(365) + cand["_off"]
    hits = cand.merge(
        ev[["code", "_dayi"]].drop_duplicates()
        .rename(columns={"_dayi": "_pd"}),
        left_on=["code", "_cd"], right_on=["code", "_pd"], how="left",
    )
    stable = (
        hits.groupby(["code", "_dayi"], sort=False)["_pd"].count()
        .rename("_hits").reset_index()
    )
    ev = ev.merge(stable, on=["code", "_dayi"], how="left")
    # >0 matched offsets (fillna first — pure-bool compare) = the 0/1
    # per-event flag; never the raw hit count, or stable_events would
    # exceed deliveries.
    ev["stable"] = (ev["_hits"].fillna(0) > 0).astype("int64")

    ev["_day"] = ev["_dayi"].astype("float64")
    return ev


def _anchor_windows(close_df: pd.DataFrame) -> pd.DataFrame:
    """Per (code, calendar year) window anchors: the year's LAST data date
    plus the window-start epoch day (anchor - DELIVERIES_WINDOW_YEARS
    calendar years + 1 day; Feb-29 anchors clamp to Feb-28 shifted)."""
    c = close_df[["code", "date"]].copy()
    c["_y"] = c["date"].dt.year.astype("int64")
    a = (
        c.groupby(["code", "_y"], sort=False)["date"].max()
        .rename("window_end_date").reset_index()
    )
    a["_m"] = a["window_end_date"].dt.month.astype("int64")
    a["_d"] = a["window_end_date"].dt.day.astype("int64")
    a["_end_day_f"] = (
        a["window_end_date"].astype("int64").astype("float64") / _US_PER_DAY
    )
    sy = a["_y"] - np.int64(DELIVERIES_WINDOW_YEARS)
    sd = a["_d"].where((a["_m"] != 2) | (a["_d"] != 29), np.int64(28))
    a["_ws_day"] = _ymd_to_epoch_day(sy, a["_m"], sd) + np.int64(1)
    return a


def compute_dividend_deliveries(
    close_df: pd.DataFrame, events_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute the rolling 5y / 1y-stride delivery audit.

    Args:
        close_df: whole-history rows sorted by (code, date) with columns
            code, date (datetime64[us]), close (float64), eps (float64,
            optional — NULL when the sec_type has no EPS source).
        events_df: delivery events with columns code, ex_dividend_date
            (datetime64[us]), dividend_per_share_pre_tax (float64 > 0).

    Returns:
        DataFrame with columns ``_OUTPUT_COLUMNS``; payer codes only,
        anchors from the code's first delivery onward.
    """
    empty = pd.DataFrame(columns=_OUTPUT_COLUMNS)
    required_close = {"code", "date", "close"}
    required_events = {"code", "ex_dividend_date", "dividend_per_share_pre_tax"}
    if (
        close_df is None or close_df.empty
        or events_df is None or events_df.empty
        or not required_close.issubset(safe_columns(close_df))
        or not required_events.issubset(safe_columns(events_df))
    ):
        return empty

    ev = _prepare_events(close_df, events_df)
    if ev.empty:
        return empty

    anchors = _anchor_windows(close_df)
    # Payer codes only + anchors at/after the first delivery (the start of
    # the dividend program).
    first_ev = (
        ev.groupby("code", sort=False)["date"].min()
        .rename("_first_ev").reset_index()
    )
    anchors = anchors.merge(first_ev, on="code", how="inner")
    anchors = anchors[anchors["window_end_date"] >= anchors["_first_ev"]]
    if anchors.empty:
        return empty

    # Window aggregation: ONE per-code cross-merge of anchors x events,
    # filtered to the window bounds. Day floats are exact (integral day
    # counts < 2^53), so the boundary comparisons are exact too.
    pairs = anchors[["code", "_end_day_f", "_ws_day"]].merge(
        ev[["code", "_day", "div", "yield", "eps", "stable", "_y"]],
        on="code",
    )
    pairs["_ws_day_f"] = pairs["_ws_day"].astype("float64")
    w = pairs[
        (pairs["_day"] >= pairs["_ws_day_f"])
        & (pairs["_day"] <= pairs["_end_day_f"])
    ]

    # Trailing-1y leg: per-event yields inside the FINAL 365d of each
    # window — an elementwise where-chain (NaN outside the slice; the
    # grouped sum skips those legs, and an all-NaN slice group sums to
    # 0.0 = "nothing paid in the trailing year", the truthful value).
    w = w.assign(_ly=w["yield"].where(w["_day"] >= w["_end_day_f"] - 365.0))

    # Grouped single-agg series + concat (the cuDF-safe pattern): means are
    # sum/count pairs so NaN yield/eps legs drop out of their own mean only.
    keys = ["code", "_end_day_f"]
    g = w.groupby(keys, sort=False)
    agg = pd.concat([
        g["div"].count().rename("deliveries"),
        g["stable"].sum().rename("stable_events"),
        w[["code", "_end_day_f", "_y"]].drop_duplicates()
            .groupby(keys, sort=False)["_y"].count()
            .rename("years_with_delivery"),
        g["yield"].sum().rename("_yls"),
        g["yield"].count().rename("_ylc"),
        g["_ly"].sum().rename("_lys"),
        g["eps"].sum().rename("_eps"),
        g["eps"].count().rename("_epc"),
    ], axis=1).reset_index()

    # Full anchor grid (zero-delivery windows keep their row: 0 counts,
    # NULL aggregates).
    out = anchors[["code", "window_end_date", "_ws_day", "_end_day_f"]].merge(
        agg, on=["code", "_end_day_f"], how="left",
    )
    out["deliveries"] = out["deliveries"].fillna(0).astype("int64")
    out["stable_events"] = out["stable_events"].fillna(0).astype("int64")
    out["years_with_delivery"] = (
        out["years_with_delivery"].fillna(0).astype("int64")
    )
    n_pos = out["deliveries"].astype("float64").where(out["deliveries"] > 0)
    out["stability"] = out["stable_events"].astype("float64") / n_pos
    out["avg_yield"] = out["_yls"] / out["_ylc"].astype("float64")
    # Window span in years (inclusive; every window is anchor-5y+1d long,
    # so closed windows annualize over ~5.003 and the anchor grid never
    # produces a shorter divisor).
    years_span = (
        out["_end_day_f"] - out["_ws_day"].astype("float64") + 1.0
    ) / 365.0
    out["annualized_yield"] = out["_yls"] / years_span
    out["latest_year_yield"] = out["_lys"]
    out["avg_eps"] = out["_eps"] / out["_epc"].astype("float64")

    out["window_start_date"] = (
        out["_ws_day"].astype("int64") * np.int64(86_400_000_000)
    ).astype("datetime64[us]")
    out = out.sort_values(["code", "window_end_date"], kind="stable")
    return out.loc[:, _OUTPUT_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------
#  Write (force = DELETE + COPY; otherwise wholesale upsert — tiny table)
# ---------------------------------------------------------------------------
async def run_dividend_deliveries(
    conn, sec_type: str, close_df: pd.DataFrame, events_df: pd.DataFrame, *,
    force: bool, code: str | None,
) -> int:
    """Compute the delivery audit and write DELIVERIES_TABLE.

    - force: DELETE sec_type rows + chunked COPY-insert + ANALYZE.
    - otherwise (incremental / single-code): wholesale UPSERT of the
      recomputed scope — the current-year anchor and any late-arriving
      event are mutable, and the table is tiny (~payers x years rows).
    """
    label = DELIVERIES_TABLE.split(".")[-1]
    frame = compute_dividend_deliveries(close_df, events_df)
    n_codes = int(frame["code"].nunique()) if not frame.empty else 0
    logger.info(f"  [{sec_type}] {label}: {len(frame):,} window rows "
                f"({n_codes:,} payer codes)")
    if frame.empty:
        logger.info(f"  [{sec_type}] {label}: no window rows; nothing to write")
        return 0
    frame = frame.copy()
    frame.insert(0, "sec_type", sec_type)

    if force:
        logger.info(f"  [{sec_type}] Deleting existing {sec_type} rows from "
                    f"{DELIVERIES_TABLE}...")
        await chunked_purge_async(
            conn, DELIVERIES_TABLE, where_sql="sec_type = $1", params=(sec_type,)
        )
        n = await copy_frame_chunked_async(
            conn, DELIVERIES_TABLE, frame,
            columns=_WRITE_COLUMNS,
            numeric_cols=_WRITE_NUMERIC_COLS, round_to=6,
            date_cols=_WRITE_DATE_COLS,
            label=label,
        )
        await analyze_tables_async(conn, [DELIVERIES_TABLE])
        logger.info(f"  [{sec_type}]   inserted {n:,} rows (COPY)")
        return n

    rows = sanitize_for_db_insert(
        frame[_WRITE_COLUMNS],
        numeric_cols=_WRITE_NUMERIC_COLS, round_to=6,
        date_cols=_WRITE_DATE_COLS,
    )
    logger.info(f"  [{sec_type}] Wholesale-upserting {len(rows):,} rows into "
                f"{DELIVERIES_TABLE}...")
    n_copied, n_upserted = await copy_or_upsert_split_async(
        conn, DELIVERIES_TABLE, rows,
        key_columns=["sec_type", "code", "window_end_date"],
        date_column="window_end_date",
        partition_key="code",
    )
    n = n_copied + n_upserted
    logger.info(f"  [{sec_type}]   inserted {n:,} rows via "
                f"{'COPY' if n_upserted == 0 else 'COPY+upsert mix'}")
    return n
