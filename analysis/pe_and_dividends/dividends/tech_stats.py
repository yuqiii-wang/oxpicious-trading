"""Dividend price ESTIMATION curve — the ``tech`` stage of
analysis.pe_and_dividends (2026-10-03, ETF scope only).

MODEL (per code; segments delimited by the code's ex-dividend dates
e_1 < ... < e_n — stats.etf_adjustment rows with
implied_dividend_per_share > 0):

  Completed segment i (t in [e_i, e_{i+1})):
    est(t) = close(e_i) * exp(r_i * days(t - e_i))
    r_i    = ln((close(e_{i+1}) + D_{i+1}) / close(e_i))
             / days(e_{i+1} - e_i - 1 calendar day)
  The curve anchors on the POST-DROP close of the ex-date (the exchange's
  ex-dividend reference convention: record-date close minus the dividend)
  and grows exponentially to the next cum-dividend level
  close(e_{i+1}) + D_{i+1} exactly ON THE RECORD DATE (the last calendar
  day before the next ex-date) — the "record-date full value, next day
  minus the dividend" sawtooth.

  Open TAIL segment (t >= e_n):
    est(t) = close(e_n) * exp(r_tail * days(t - e_n))
    r_tail = ln(1 + D_n / close(e_n)) / freq_days
    freq_days = median of the last up-to-TECH_FREQ_GAP_COUNT inter-ex-date
    calendar-day gaps (TECH_FALLBACK_FREQ_DAYS when only one event).

Incremental contract: tail rows are MUTABLE — when the next ex-date
lands, the whole former tail is recomputed with realized anchors, so each
run re-upserts every row from the STORED tail's seg_start_date onward
(plus missing dates). Completed segments are immutable.

COMPUTE LAYOUT (cudf GPU-first): one whole-frame pass over the sorted
(code, date) frame — groupby cumsum for segment ids, per-code ffill of
the ex-date anchor columns, and an event-row subframe whose shift(-1)
provides the next-event anchor values propagated back via ffill (the
frame is re-sorted after each row-count-changing merge — cuDF merges do
not guarantee row-order preservation, and the ffills depend on within-
code date order). The per-code frequency/tail-rate aggregation runs on a
small events-only frame. No mid-flow host hybrids; no per-row python.
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
from _common.df_utils import grouped_diff, to_dt64
from _common.df_utils.sanitize import safe_columns

from analysis._common.sanitize import sanitize_for_db_insert
from _common.labels import SEC_TYPE_CODE
from analysis.pe_and_dividends.dividends.config import (
    TECH_FALLBACK_FREQ_DAYS,
    TECH_FREQ_GAP_COUNT,
    TECH_TABLE,
)

logger = logging.getLogger("pe_and_dividends")

_US_PER_DAY = 86_400_000_000.0  # datetime64[us] ticks per calendar day
# date.toordinal() of 1970-01-01 — converts python-date ordinals to the
# epoch-day axis used by the datetime64 arithmetic.
_EPOCH_DAY_ORDINAL = 719163

_CURVE_COLUMNS = [
    "code", "date", "div_est_price",
    "seg_start_date", "seg_end_date", "seg_next_div", "seg_rate",
]

_WRITE_COLUMNS = ["sec_type", *_CURVE_COLUMNS]
_WRITE_NUMERIC_COLS = ["div_est_price", "seg_next_div"]
_WRITE_DATE_COLS = ["date", "seg_start_date", "seg_end_date"]


# ---------------------------------------------------------------------------
#  Compute
# ---------------------------------------------------------------------------
def _tail_rate_table(events: pd.DataFrame) -> pd.DataFrame:
    """Per-code open-tail growth rate r_tail (small events-only frame).

    ``events`` holds one row per (code, ex-date), sorted by (code, date),
    with columns code, date, ex_close, ex_div. Returns one row per code
    with columns code, r_tail.

    freq_days = the median of the last up-to-TECH_FREQ_GAP_COUNT
    inter-event gaps — computed without GroupBy.median (a cuDF fallback
    risk): for <=2 kept gaps the median is their mean, for >=3 it is the
    middle gap (the one ending at _seq == n_ev - 1).
    """
    ev = events[["code", "date", "ex_close", "ex_div"]].copy()
    ev["_one"] = np.int64(1)
    ev["_seq"] = ev.groupby("code", sort=False)["_one"].cumsum()
    ev["_ex_us"] = ev["date"].astype("int64")
    # Gap of the row ending at _seq k = date_k - date_{k-1} (NaN on each
    # code's first event). sort=False: ev arrives (code, date)-sorted and
    # groupby diff follows within-group appearance order.
    grouped_diff(ev, "code", ["_ex_us"], out_names=["_gap_us"], sort=False)
    ev["_gap_days"] = ev["_gap_us"].astype("float64") / _US_PER_DAY

    # n_ev merge AFTER the diff (merges scramble row order; the diff must
    # see the sorted frame). Everything below is elementwise or
    # order-independent aggregation.
    n_ev = (
        ev.groupby("code", sort=False)["_one"].sum()
        .rename("n_ev").reset_index()
    )
    ev = ev.merge(n_ev, on="code", how="left")

    # Last up-to-3 gaps: those ending at _seq = n_ev-2, n_ev-1, n_ev.
    kept = ev[
        (ev["_seq"] >= ev["n_ev"] - np.int64(TECH_FREQ_GAP_COUNT - 1))
        & ev["_gap_days"].notna()
    ]
    k_n = kept.groupby("code", sort=False)["_gap_days"].count().rename("k_n")
    k_sum = kept.groupby("code", sort=False)["_gap_days"].sum().rename("k_sum")
    mid = (
        kept[kept["_seq"] == kept["n_ev"] - np.int64(TECH_FREQ_GAP_COUNT - 2)]
        .groupby("code", sort=False)["_gap_days"].first().rename("mid")
    )
    freq = pd.concat([k_n, k_sum, mid], axis=1).reset_index()
    mean_gap = freq["k_sum"] / freq["k_n"].astype("float64")
    freq["freq_days"] = freq["mid"].where(
        freq["k_n"] >= np.int64(TECH_FREQ_GAP_COUNT), mean_gap
    )

    # Tail anchor = the LAST event row per code (_seq == n_ev).
    tail = ev[ev["_seq"] == ev["n_ev"]][["code", "ex_close", "ex_div"]].rename(
        columns={"ex_close": "tail_close", "ex_div": "tail_div"}
    )
    tail = tail.merge(freq[["code", "freq_days"]], on="code", how="left")
    # The 365 fallback applies POST-merge: codes absent from freq (single
    # event — no gap to median) would otherwise keep a NaN that kills
    # their whole tail segment.
    tail["freq_days"] = tail["freq_days"].fillna(TECH_FALLBACK_FREQ_DAYS)
    ratio = tail["tail_div"] / tail["tail_close"].where(tail["tail_close"] > 0)
    tail["r_tail"] = np.log1p(ratio) / tail["freq_days"]
    return tail[["code", "r_tail"]]


def compute_dividend_est_curve(etf_df: pd.DataFrame) -> pd.DataFrame:
    """Compute the per-(code, date) dividend price estimation curve.

    Args:
        etf_df: whole-history ETF rows sorted by (code, date) with columns
            code, date (datetime64[us]), close (float64, non-null),
            implied_dividend_per_share (float64, 0 when no event).

    Returns:
        DataFrame with columns ``_CURVE_COLUMNS``; rows only from each
        code's first ex-dividend date onward (non-payers emit nothing).
    """
    empty = pd.DataFrame(columns=_CURVE_COLUMNS)
    required = {"code", "date", "close", "implied_dividend_per_share"}
    if etf_df is None or etf_df.empty or not required.issubset(safe_columns(etf_df)):
        return empty

    df = etf_df[["code", "date", "close", "implied_dividend_per_share"]].copy()
    df = df.sort_values(["code", "date"], kind="stable").reset_index(drop=True)

    # --- Event flag + 1-based segment ids (0 = before the first ex) ------
    # All groupbys run on the frame BY COLUMN NAME (groupby(series) breaks
    # the cuDF fast path); masked helper columns are assigned to the frame
    # BEFORE their ffill/cumsum. Datetime masking runs in int64 [us]-tick
    # space — Series.where on a datetime column has no cuDF fast path —
    # with a cast back to datetime64[us] only at the output boundary.
    div_amt = df["implied_dividend_per_share"].fillna(0.0)
    is_ex = (div_amt > 0) & (df["close"] > 0)
    df["_flag"] = is_ex.astype("int64")
    df["seg_id"] = df.groupby("code", sort=False)["_flag"].cumsum()

    date_us = df["date"].astype("int64")

    # --- Anchor columns: ex-row values forward-filled within the code ----
    # (mask FIRST — non-event rows nulled — ffill second; the other order
    # would leak non-ex closes into the anchors. Datetime masking runs in
    # float64 [us]-tick space — Series.where on a datetime column has no
    # cuDF fast path, and an int 0-sentinel would NOT be honored by ffill;
    # NaN float64 ticks are exact (< 2^53) and cast back losslessly. The
    # int→datetime cast happens on the valid-sliced OUTPUT only.)
    df["_masked_close"] = df["close"].where(is_ex)
    df["_anchor_close"] = (
        df.groupby("code", sort=False)["_masked_close"].ffill()
    )
    df["_masked_us_f"] = df["date"].astype("int64").astype("float64").where(is_ex)
    df["_anchor_us_f"] = (
        df.groupby("code", sort=False)["_masked_us_f"].ffill()
    )

    # --- Next-event values: shift(-1) ON the event rows ------------------
    ev = df[is_ex][["code", "date", "close", "implied_dividend_per_share"]].rename(
        columns={
            "close": "ex_close",
            "implied_dividend_per_share": "ex_div",
        }
    )
    if ev.empty:
        return empty
    nxt = ev[["code", "date"]].copy()
    nxt["_next_close"] = ev.groupby("code", sort=False)["ex_close"].shift(-1)
    nxt["_next_div"] = ev.groupby("code", sort=False)["ex_div"].shift(-1)
    nxt["_next_date"] = ev.groupby("code", sort=False)["date"].shift(-1)
    df = df.merge(nxt, on=["code", "date"], how="left")
    # cuDF merges do not guarantee row-order preservation — restore the
    # (code, date) order the ffills below depend on.
    df = df.sort_values(["code", "date"], kind="stable").reset_index(drop=True)
    for col in ("_next_close", "_next_div", "_next_date"):
        df[col] = df.groupby("code", sort=False)[col].ffill()
    # date ticks MUST be recomputed here: the merge + re-sort above may
    # reorder rows, and the pre-merge ``date_us`` Series no longer aligns
    # with df's positions (the helper _masked_us_f/_anchor_us_f columns
    # live ON the frame and travel through the merge correctly; a
    # standalone Series does not).
    date_us = df["date"].astype("int64")
    next_us = df["_next_date"].astype("int64")

    # --- Segment arithmetic (all elementwise on row columns) -------------
    days_since = (date_us.astype("float64") - df["_anchor_us_f"]) / _US_PER_DAY
    # Segment terminal = the RECORD date (next ex-date - 1 calendar day):
    # the curve reaches the full cum-dividend level exactly there, then
    # drops by the dividend at the ex-date anchor reset — the
    # "record-date full value, next day minus the dividend" convention.
    days_to_next = (
        (next_us.astype("float64") - df["_anchor_us_f"]) / _US_PER_DAY - 1.0
    )

    ratio = (df["_next_close"] + df["_next_div"]) / df["_anchor_close"].where(
        df["_anchor_close"] > 0
    )
    # Guard the degenerate 0/negative spans (tail rows carry the previous
    # segment's next_* — days_to_next collapses to <= 0 there).
    r_completed = np.log(ratio) / days_to_next.where(days_to_next > 0)

    # --- Tail identification + tail rate ----------------------------------
    n_ev_all = (
        ev.groupby("code", sort=False)["ex_div"].count().rename("n_ev").reset_index()
    )
    df = df.merge(n_ev_all, on="code", how="left")
    tail_mask = df["seg_id"] == df["n_ev"]

    df["_r"] = r_completed.where(~tail_mask)
    if bool(tail_mask.any()):
        tail_rates = _tail_rate_table(ev)
        df = df.merge(tail_rates, on="code", how="left")
        # Tail rows take r_tail; everything else keeps r_completed (fill
        # only where _r is null AND the row is a tail row — pre-first-ex
        # rows stay null and drop out of the output).
        df["_r"] = df["_r"].fillna(df["r_tail"].where(tail_mask))
        df = df.drop(columns=["r_tail"])

    # --- Curve value + validity mask --------------------------------------
    est = df["_anchor_close"] * np.exp(df["_r"] * days_since)
    valid = (
        (df["seg_id"] > 0)
        & df["_anchor_close"].notna()
        & est.notna()
        & (est > 0)
        & (est < 1e12)
    )

    # Output projection. Tail rows carry the PREVIOUS segment's ffilled
    # next_* — null them (open tail has no next ex-date) via the int-tick
    # NaT sentinel on the date leg. Date casts run on the valid-SLICED
    # frame (the float anchor ticks hold NaN pre-first-ex, which cannot
    # int-cast) and via a frame column (astype on the raw proxy ndarray
    # has no cuDF fast path).
    df["div_est_price"] = est
    df["_seg_end_us"] = np.where(tail_mask, np.int64(-(1 << 63)), next_us)
    df["seg_next_div"] = df["_next_div"].where(~tail_mask)
    df["seg_rate"] = df["_r"]
    out = df.loc[
        valid, ["code", "date", "div_est_price", "_anchor_us_f",
                "_seg_end_us", "seg_next_div", "seg_rate"],
    ]
    out["seg_start_date"] = (
        out["_anchor_us_f"].astype("int64").astype("datetime64[us]")
    )
    out["seg_end_date"] = out["_seg_end_us"].astype("datetime64[us]")
    return out.loc[:, _CURVE_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------
#  Write (force = DELETE + COPY; incremental = missing dates + stored tail)
# ---------------------------------------------------------------------------
async def _stored_tail_starts(conn, sec_type: str) -> pd.DataFrame:
    """Per-code epoch-day of the STORED open tail's seg_start_date
    (seg_end_date IS NULL) — the mutable-window boundary for incremental
    re-upserts."""
    rows = await conn.fetch(
        f"""
        SELECT code, MIN(seg_start_date) AS tail_start
        FROM {TECH_TABLE}
        WHERE sec_type = $1 AND seg_end_date IS NULL
        GROUP BY code
        """,
        SEC_TYPE_CODE[sec_type],
    )
    if not rows:
        return pd.DataFrame(columns=["code", "_tail_day"])
    cols = {
        "code": [r["code"] for r in rows],
        "_tail_day": [
            float(r["tail_start"].toordinal() - _EPOCH_DAY_ORDINAL)
            for r in rows
        ],
    }
    return pd.DataFrame(cols, columns=["code", "_tail_day"])


async def run_dividend_tech(
    conn, sec_type: str, etf_df: pd.DataFrame, *,
    force: bool, code: str | None, target_dates: set | None,
) -> int:
    """Compute the estimation curve from ``etf_df`` and write TECH_TABLE.

    - force: DELETE sec_type rows + chunked COPY-insert + ANALYZE.
    - single-code (``code``): upsert ALL rows of that code (full recompute
      for the one security — the --code contract of the other stages).
    - incremental: upsert rows whose date is in ``target_dates`` OR at/
      after the code's STORED tail start (mutable window — the former
      tail is rewritten with realized anchors once the next ex-date
      lands). Codes with no stored tail compare True (full rewrite),
      which only arises for new payers whose dates are missing anyway.
    """
    label = TECH_TABLE.split(".")[-1]
    curve = compute_dividend_est_curve(etf_df)
    n_codes = int(curve["code"].nunique()) if not curve.empty else 0
    logger.info(f"  [{sec_type}] {label}: {len(curve):,} curve rows "
                f"({n_codes:,} codes)")
    if curve.empty:
        logger.info(f"  [{sec_type}] {label}: no curve rows; nothing to write")
        return 0
    frame = curve.copy()
    frame.insert(0, "sec_type", np.int16(SEC_TYPE_CODE[sec_type]))

    if force:
        logger.info(f"  [{sec_type}] Deleting existing {sec_type} rows from "
                    f"{TECH_TABLE}...")
        await chunked_purge_async(
            conn, TECH_TABLE, where_sql="sec_type = $1",
            params=(SEC_TYPE_CODE[sec_type],)
        )
        n = await copy_frame_chunked_async(
            conn, TECH_TABLE, frame,
            columns=_WRITE_COLUMNS,
            numeric_cols=_WRITE_NUMERIC_COLS, round_to=6,
            date_cols=_WRITE_DATE_COLS,
            label=label,
        )
        await analyze_tables_async(conn, [TECH_TABLE])
        logger.info(f"  [{sec_type}]   inserted {n:,} rows (COPY)")
        return n

    # ---- Incremental / single-code: build the row subset -----------------
    if code is None and target_dates is not None and len(target_dates) == 0:
        # No missing dates — but the tail window may still need refreshing
        # (fall through; the filter below decides).
        pass

    sub = frame
    if code is None and target_dates is not None:
        td64 = to_dt64(sorted(target_dates))
        in_targets = frame["date"].isin(td64)
        tail_starts = await _stored_tail_starts(conn, sec_type)
        merged = frame[["code"]].merge(tail_starts, on="code", how="left")
        # Epoch-day axis both sides: [us] ints / 86400e6 vs toordinal shift.
        date_day = frame["date"].astype("int64").astype("float64") / _US_PER_DAY
        in_tail_window = date_day >= merged["_tail_day"].fillna(float("-inf"))
        sub = frame[in_targets | in_tail_window]
        logger.info(f"  [{sec_type}] {label} incremental filter: "
                    f"{len(sub):,} of {len(frame):,} rows "
                    f"(missing-dates ∪ stored-tail window)")
    elif code is not None:
        logger.info(f"  [{sec_type}] {label} single-code mode: upserting all "
                    f"{len(sub):,} rows of {code}")

    if sub.empty:
        logger.info(f"  [{sec_type}] {label}: no rows to write")
        return 0

    rows = sanitize_for_db_insert(
        sub[_WRITE_COLUMNS],
        numeric_cols=_WRITE_NUMERIC_COLS, round_to=6,
        date_cols=_WRITE_DATE_COLS,
    )
    logger.info(f"  [{sec_type}] Upserting {len(rows):,} rows into {TECH_TABLE}...")
    n_copied, n_upserted = await copy_or_upsert_split_async(
        conn, TECH_TABLE, rows,
        key_columns=["sec_type", "code", "date"],
    )
    n = n_copied + n_upserted
    logger.info(f"  [{sec_type}]   inserted {n:,} rows via "
                f"{'COPY' if n_upserted == 0 else 'COPY+upsert mix'}")
    return n
