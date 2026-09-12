"""Day-close mirror: analysis_signals.signals → live.live_signals.

``--live`` companion of analyze.analysis_signals (wired in __main__.py):
every signal row NOT yet mirrored gets ONE day-close observation in
live.live_signals at the session close (time = 15:00:00,
is_day_close_trigger = TRUE — the intraday runs leave the column
FALSE):

  - mov_std: value = the day's CLOSE (the same adjusted-price
    convention the bands were computed on — fetch._PRICE_SOURCE),
    compared against the crossed band level (signal_threshold) —
    price space;
  - mov_rsi: value = the day's rsi_{W}days (analysis.mov_ave_rsi,
    wide table), compared against the top/bottom-1% percentile
    threshold — indicator space (an RSI value is not a price);
  - mov_gap: value = the day's gap_{W}days N-day price return
    (analysis.mov_ave_rsi, wide table), compared against the
    top/bottom-1% percentile threshold — the same indicator-space
    machinery as mov_rsi (rank-based percentile, unbounded values).
  - high_low_streaks: value = the ANCHOR day's CLOSE (price space —
    mov_std's pattern), compared against the streak side's band edge
    (signal_threshold = high_val for top / low_val for bottom; the
    anchor day is out-of-band, so the still-breaches re-verification
    holds by construction). No breach re-verification for the ~quarter
    of anchors landing on BRIDGED IN-BAND days (the mid of the SPAN
    can sit inside the band — see _finalize's require_breach).
  - mov_pairs / mov_pairs_ema: value = the day's stored spread
    (ma5_vs_ma{W} / ema6_vs_ema{W}, wide tables), compared against 0 —
    the crossed level IS the zero line, so signal_excess reads as the
    spread's signed distance from 0 on the cross day.
  - px_vol: value = the day's recorded px_t (the
    analysis.mov_ave_price_vs_amt registry), compared against the
    speed's t-bar SIGNED BY SIDE (analysis_signals stores the |bar|;
    the mirror signs it +top / −bottom so the buy-side comparison and
    the excess are meaningful: excess = t − signed_bar).
  - margin_ratio: value = the day's ratio z-score recomputed by SQL
    window functions over the code's own trailing 1220 non-NULL ratio
    rows (min 250, shifted 1 row — the same definition the signal
    detection used), compared against the state's z-bar SIGNED BY SIDE
    (vlow/low → −|bar|, high/vhigh → +|bar|).

Only PK-missing rows are written (anti-join on the live PK at 15:00),
so re-running is idempotent and an old-date refresh backfills exactly
the missing rows. Rows whose day value is missing (no close / no RSI
row / undefined z) or no longer breaches (source-data revision) are
skipped — live.live_signals stays a pure breach record (the
high_low_streaks anchors are the documented exception: a streak's mid
day, kept unconditionally).

Implementation note: no pandas — asyncpg Record lists and object-dtype
date columns poison cudf.pandas frames (fallback cascade). Columns are
extracted on the host with plain tuples + numpy instead.
"""
from __future__ import annotations

from datetime import time

import numpy as np

from _common.db_commons import bulk_upsert_async
from analyze.analysis_forecasts.config import (
    GAP_WINDOWS,
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
    RSI_WINDOWS,
)
from analyze.analysis_forecasts.fetch import _PRICE_SOURCE
from analyze.analysis_signals.config import TABLE_SIGNALS
from live.live_signals.config import (
    LIVE_SIGNAL_PK,
    LIVE_SIGNALS_TABLE,
    SIGNAL_PCT_SCALE,
    SIGNAL_SCALE,
)

# Session close — the day-close observation time in live.live_signals
# (a PK column; distinguishes day-close rows from intraday-bar rows).
DAY_CLOSE_TIME = time(15, 0)

# Signal columns the day-close rows carry (mirrors LIVE_SIGNAL_COLUMNS
# + the is_day_close_trigger flag).
_RECORD_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
    "action", "signal_excess", "signal_excess_pct", "signal",
    "signal_threshold", "confidence", "is_day_close_trigger",
]

# NOT-yet-mirrored filter: anti-join on the live PK at the close time.
_ANTI_JOIN = f"""
  AND NOT EXISTS (
      SELECT 1 FROM {LIVE_SIGNALS_TABLE} l
      WHERE l.code = s.code
        AND l.sec_type = s.sec_type
        AND l.signal_type = s.signal_type
        AND l.signal_sub_type = s.signal_sub_type
        AND l.date = s.date
        AND l.time = $2
  )
"""

# rsi{W} / gap{W} sub_type names — column-position lookup via broadcast
# equality (order-agnostic, exact match): the engines only emit
# sub_types built from RSI_WINDOWS / GAP_WINDOWS, so every key matches
# exactly one column position.
_RSI_SUB_TYPES = np.asarray([f"rsi{w}" for w in RSI_WINDOWS])
_GAP_SUB_TYPES = np.asarray([f"gap{w}" for w in GAP_WINDOWS])

# pair{W} / emapair{W} sub_type names for the cross families (same
# indicator-space lookup; the spread columns arrive aliased to the
# matrix-key naming pair_{W} / ema_pair_{W}).
_PAIRS_SUB_TYPES = np.asarray([f"pair{w}" for w in MOV_PAIRS_WINDOWS])
_EMA_PAIRS_SUB_TYPES = np.asarray(
    [f"emapair{w}" for w in MOV_PAIRS_EMA_WINDOWS]
)

# Half-quantum of analysis_signals.signals.signal_threshold NUMERIC(14,6):
# the stored threshold is rounded at write time, so the mirror's
# re-verification must tolerate ±5e-7 or boundary rows (day value within
# rounding distance of the threshold) are wrongly skipped. A real source
# revision moves the day value far more than that, so the safety net is
# unaffected.
_THR_TOL = 5e-7


async def _fetch_missing_rsi(conn, sec_type: str) -> list:
    """mov_rsi signal rows not yet mirrored, joined with the day's RSI
    columns (analysis.mov_ave_rsi is WIDE — one row per code/date).

    Returns the raw asyncpg rows; columns are extracted on the host."""
    rsi_cols = ", ".join(
        f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
    )
    return await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_sub_type, s.date, s.action, "
        f"       s.signal_threshold::float8 AS thr, "
        f"       ROUND(s.confidence * 100)::int AS confidence, "
        f"       {rsi_cols} "
        f"FROM {TABLE_SIGNALS} s "
        f"JOIN analysis.mov_ave_rsi r "
        f"  ON r.sec_type = s.sec_type AND r.code = s.code "
        f" AND r.date = s.date "
        f"WHERE s.sec_type = $1 AND s.signal_type = 'mov_rsi' "
        f"{_ANTI_JOIN} "
        f"ORDER BY s.code, s.date",
        sec_type, DAY_CLOSE_TIME,
    )


async def _fetch_missing_gap(conn, sec_type: str) -> list:
    """mov_gap signal rows not yet mirrored, joined with the day's
    gap_{W}days N-day price-return columns (analysis.mov_ave_rsi is
    WIDE — one row per code/date).

    Returns the raw asyncpg rows; columns are extracted on the host."""
    gap_cols = ", ".join(
        f"r.gap_{w}days::float8 AS gap_{w}days" for w in GAP_WINDOWS
    )
    return await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_sub_type, s.date, s.action, "
        f"       s.signal_threshold::float8 AS thr, "
        f"       ROUND(s.confidence * 100)::int AS confidence, "
        f"       {gap_cols} "
        f"FROM {TABLE_SIGNALS} s "
        f"JOIN analysis.mov_ave_rsi r "
        f"  ON r.sec_type = s.sec_type AND r.code = s.code "
        f" AND r.date = s.date "
        f"WHERE s.sec_type = $1 AND s.signal_type = 'mov_gap' "
        f"{_ANTI_JOIN} "
        f"ORDER BY s.code, s.date",
        sec_type, DAY_CLOSE_TIME,
    )


async def _fetch_missing_std(conn, sec_type: str) -> list:
    """mov_std signal rows not yet mirrored, joined with the day's close
    (the SAME adjusted-price convention the bands were computed on).

    Returns the raw asyncpg rows; columns are extracted on the host."""
    base, price_expr = _PRICE_SOURCE[sec_type]
    return await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_sub_type, s.date, s.action, "
        f"       s.signal_threshold::float8 AS thr, "
        f"       ROUND(s.confidence * 100)::int AS confidence, "
        f"       {price_expr}::float8 AS close "
        f"FROM {TABLE_SIGNALS} s "
        f"JOIN {base} ON b.code = s.code AND b.date = s.date "
        f"WHERE s.sec_type = $1 AND s.signal_type = 'mov_std' "
        f"  AND b.close IS NOT NULL "
        f"{_ANTI_JOIN} "
        f"ORDER BY s.code, s.date",
        sec_type, DAY_CLOSE_TIME,
    )


async def _fetch_missing_hls(conn, sec_type: str) -> list:
    """high_low_streaks signal rows not yet mirrored, joined with the
    ANCHOR day's close (price space — signal_threshold is the streak
    side's band edge, high_val for top / low_val for bottom, so the
    close-below/above re-verification in _finalize holds by
    construction: the anchor day is out-of-band). Same shape as
    mov_std's fetch; only the signal_type differs.

    Returns the raw asyncpg rows; columns are extracted on the host."""
    base, price_expr = _PRICE_SOURCE[sec_type]
    return await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_sub_type, s.date, s.action, "
        f"       s.signal_threshold::float8 AS thr, "
        f"       ROUND(s.confidence * 100)::int AS confidence, "
        f"       {price_expr}::float8 AS close "
        f"FROM {TABLE_SIGNALS} s "
        f"JOIN {base} ON b.code = s.code AND b.date = s.date "
        f"WHERE s.sec_type = $1 AND s.signal_type = 'high_low_streaks' "
        f"  AND b.close IS NOT NULL "
        f"{_ANTI_JOIN} "
        f"ORDER BY s.code, s.date",
        sec_type, DAY_CLOSE_TIME,
    )


def _finalize(
    sec_type: str,
    signal_type: str,
    code: np.ndarray,
    sub: np.ndarray,
    dates: np.ndarray,
    action: np.ndarray,
    thr: np.ndarray,
    value: np.ndarray,
    confidence: np.ndarray,
    *,
    require_breach: bool = True,
) -> list[dict]:
    """Keep rows that still breach in the signal's action direction
    (sell → at/above the threshold, buy → at/below — mov_rsi detection
    is inclusive, and the stored threshold is rounded, hence the ±_THR_TOL
    slack) and build the upsert records. NaN day values fail the
    comparisons and drop out. Confidence arrives already on the live
    0-100 scale (the fetches select ROUND(s.confidence * 100)::int —
    exact NUMERIC rounding, ×100 of the source's reverse_prob
    probability) and is passed through.

    require_breach=False (the high_low_streaks family): keep EVERY row
    regardless of the value/threshold side — the signal day is a
    streak's MEAN-MID anchor, not a threshold crossing, and ~a quarter
    of anchors land on BRIDGED IN-BAND days (the mid of the span, whose
    <=5-day re-entries stay inside the streak) whose close sits on the
    wrong side of the band edge. value still carries the anchor day's
    close vs the band edge, so signal_excess's sign tells the reader
    whether the mid sat outside (negative for buy / positive for sell)
    or inside the band.

    signal is stored at SIGNAL_SCALE decimals; signal_excess is computed
    FROM the rounded value so the stored identity signal_excess =
    signal - signal_threshold holds exactly. signal_excess_pct =
    signal_excess / |signal_threshold| * 100 (SIGNAL_PCT_SCALE decimals);
    threshold = 0 yields NaN which asyncpg maps to NULL."""
    if require_breach:
        sell = action == "sell"
        sel = np.nonzero(
            (sell & (value >= thr - _THR_TOL))
            | (~sell & (value <= thr + _THR_TOL))
        )[0]
    else:
        sel = np.arange(value.size)
    n = sel.size
    if n == 0:
        return []
    sig_val = np.round(value[sel], SIGNAL_SCALE)
    thr_sel = thr[sel]
    sig_excess: np.ndarray = sig_val - thr_sel
    # signal_excess_pct: vectorized, guarded against threshold = 0.
    # np.divide with where= sets divide-by-zero positions to 0; we then
    # stamp NaN on those rows so asyncpg writes NULL.
    safe_thr = np.where(thr_sel != 0, thr_sel, 1.0)
    sig_pct: np.ndarray = np.divide(
        sig_excess, np.abs(safe_thr), out=np.zeros_like(sig_excess),
        where=(thr_sel != 0),
    ) * 100
    sig_pct = np.round(sig_pct, SIGNAL_PCT_SCALE)
    sig_pct[thr_sel == 0] = np.nan
    conf_sel = confidence[sel]
    return [
        dict(zip(_RECORD_COLUMNS, row))
        for row in zip(
            code[sel].tolist(),
            [sec_type] * n,
            [signal_type] * n,
            sub[sel].tolist(),
            dates[sel].tolist(),
            [DAY_CLOSE_TIME] * n,
            action[sel].tolist(),
            sig_excess.tolist(),
            sig_pct.tolist(),
            sig_val.tolist(),
            thr_sel.tolist(),
            conf_sel.tolist(),
            [True] * n,
        )
    ]


def _records_from_indicator(
    rows: list, signal_type: str, sub_types: np.ndarray,
) -> list[dict]:
    """mov_rsi / mov_gap records: value = the day's wide-table indicator
    column (rsi_{W}days / gap_{W}days), picked per row by the sub_type's
    window (vectorized gather). Column layout: code, sec_type, sub_type,
    date, action, thr, confidence (0-100 int), <indicator columns...>."""
    if not rows:
        return []
    cols = list(zip(*rows))
    sub = np.asarray(cols[2])
    thr = np.asarray(cols[5], dtype=np.float64)
    confidence = np.asarray(cols[6], dtype=np.int64)
    mat = np.asarray(cols[7:], dtype=np.float64).T          # (n, k)
    win_idx = (sub[:, None] == sub_types[None, :]).argmax(axis=1)
    value = mat[np.arange(sub.size), win_idx]
    return _finalize(
        cols[1][0], signal_type,
        np.asarray(cols[0]), sub, np.asarray(cols[3]),
        np.asarray(cols[4]), thr, value, confidence,
    )


def _records_from_rsi(rows: list) -> list[dict]:
    """mov_rsi records: value = the day's rsi_{W}days."""
    return _records_from_indicator(rows, "mov_rsi", _RSI_SUB_TYPES)


def _records_from_gap(rows: list) -> list[dict]:
    """mov_gap records: value = the day's gap_{W}days N-day return."""
    return _records_from_indicator(rows, "mov_gap", _GAP_SUB_TYPES)


def _records_from_std(rows: list) -> list[dict]:
    """mov_std records: value = the day's close vs the band level.
    Column layout: code, sec_type, sub_type, date, action, thr,
    confidence (0-100 int), close."""
    if not rows:
        return []
    cols = list(zip(*rows))
    return _finalize(
        cols[1][0], "mov_std",
        np.asarray(cols[0]), np.asarray(cols[2]), np.asarray(cols[3]),
        np.asarray(cols[4]), np.asarray(cols[5], dtype=np.float64),
        np.asarray(cols[7], dtype=np.float64),
        np.asarray(cols[6], dtype=np.int64),
    )


def _records_from_hls(rows: list) -> list[dict]:
    """high_low_streaks records: value = the ANCHOR day's close vs the
    streak side's band edge (signal_threshold). Same column layout as
    mov_std's fetch; NO breach re-verification (see _finalize's
    require_breach — the anchor is a streak's mid day, not a threshold
    crossing)."""
    if not rows:
        return []
    cols = list(zip(*rows))
    return _finalize(
        cols[1][0], "high_low_streaks",
        np.asarray(cols[0]), np.asarray(cols[2]), np.asarray(cols[3]),
        np.asarray(cols[4]), np.asarray(cols[5], dtype=np.float64),
        np.asarray(cols[7], dtype=np.float64),
        np.asarray(cols[6], dtype=np.int64),
        require_breach=False,
    )


async def _fetch_missing_pairs(
    conn, sec_type: str, signal_type: str, spread_table: str,
    spread_col_fmt: str, windows, alias_fmt: str,
) -> list:
    """mov_pairs / mov_pairs_ema signal rows not yet mirrored, joined
    with the day's stored relative-spread columns (the WIDE detail
    tables — one row per sec_type/code/date, the same indicator-space
    pattern as mov_rsi's fetch). The crossed level is the ZERO line
    (signal_threshold = 0), so the still-breaches re-verification
    holds by construction on the cross day.

    Returns the raw asyncpg rows; columns are extracted on the host."""
    spread_cols = ",\n       ".join(
        f"d.{spread_col_fmt.format(w=w)}::float8 AS {alias_fmt.format(w=w)}"
        for w in windows
    )
    # signal_type inlined (module-internal literal) so the shared
    # _ANTI_JOIN's $2 stays the TIME parameter, like every other fetch.
    return await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_sub_type, s.date, s.action, "
        f"       s.signal_threshold::float8 AS thr, "
        f"       ROUND(s.confidence * 100)::int AS confidence, "
        f"       {spread_cols} "
        f"FROM {TABLE_SIGNALS} s "
        f"JOIN {spread_table} d "
        f"  ON d.sec_type = s.sec_type AND d.code = s.code "
        f" AND d.date = s.date "
        f"WHERE s.sec_type = $1 AND s.signal_type = {signal_type!r} "
        f"{_ANTI_JOIN} "
        f"ORDER BY s.code, s.date",
        sec_type, DAY_CLOSE_TIME,
    )


def _records_from_pairs(rows: list, signal_type: str,
                        sub_types: np.ndarray) -> list[dict]:
    """mov_pairs / mov_pairs_ema records: value = the day's stored
    spread vs the zero line (the crossed level). Column layout: code,
    sec_type, sub_type, date, action, thr, confidence (0-100 int),
    <spread columns...>."""
    return _records_from_indicator(rows, signal_type, sub_types)


async def _fetch_missing_pxvol(conn, sec_type: str) -> list:
    """px_vol signal rows not yet mirrored, joined with the day's
    recorded px_t from the analysis.mov_ave_price_vs_amt registry (the
    family's date-level source of truth — the mirror audits against
    the recorded state evidence, not a recomputation).

    Returns the raw asyncpg rows; columns are extracted on the host."""
    return await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_sub_type, s.date, s.action, "
        f"       s.signal_threshold::float8 AS thr, "
        f"       ROUND(s.confidence * 100)::int AS confidence, "
        f"       r.px_t::float8 AS px_t "
        f"FROM {TABLE_SIGNALS} s "
        f"JOIN analysis.mov_ave_price_vs_amt r "
        f"  ON r.sec_type = s.sec_type AND r.code = s.code "
        f" AND r.date = s.date "
        f"WHERE s.sec_type = $1 AND s.signal_type = 'px_vol' "
        f"{_ANTI_JOIN} "
        f"ORDER BY s.code, s.date",
        sec_type, DAY_CLOSE_TIME,
    )


def _records_from_signed_bar(
    rows: list, signal_type: str,
) -> list[dict]:
    """px_vol / margin_ratio records: value = the day's t / z score,
    threshold = the stored |bar| SIGNED BY SIDE (+sell / −buy — the
    analysis rows record the positive bar; the mirror signs it so the
    breach comparison and the excess speak the value's own scale:
    excess = value − signed_bar). Column layout: code, sec_type,
    sub_type, date, action, thr, confidence (0-100 int), value."""
    if not rows:
        return []
    cols = list(zip(*rows))
    action = np.asarray(cols[4])
    thr = np.asarray(cols[5], dtype=np.float64)
    thr_signed = np.where(action == "sell", thr, -thr)
    return _finalize(
        cols[1][0], signal_type,
        np.asarray(cols[0]), np.asarray(cols[2]), np.asarray(cols[3]),
        action, thr_signed,
        np.asarray(cols[7], dtype=np.float64),
        np.asarray(cols[6], dtype=np.int64),
    )


# margin-ratio source table + the estimated-close row filter per
# sec_type (the frame-row space the z windows were computed on —
# mirrors analysis_forecasts.fetch._AMT_SOURCE's conventions).
_MARGIN_SOURCE = {
    "etf": (
        "stats.etf_basic_stats b "
        "LEFT JOIN stats.etf_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "AND COALESCE(b.is_close_estimated, FALSE) = FALSE",
    ),
    "stock": (
        "stats.stock_basic_stats b "
        "LEFT JOIN stats.stock_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "",
    ),
}


async def _fetch_missing_mratio(conn, sec_type: str) -> list:
    """margin_ratio signal rows not yet mirrored, joined with the day's
    ratio z-score recomputed by SQL window functions over EXACTLY the
    frame-row space and definition the detection used (ratio =
    rz_buy / trading_amount on rz_buy > 0 days, NULL otherwise; mean /
    stddev_samp / count over the 1220 FRAME rows ending one row before
    the day — avg/count ignore the NULL no-buy rows, so min 250
    non-NULL matches the pandas min_periods; window shifted 1 row =
    the grouped shift). Index has no margin data — no rows.

    Returns the raw asyncpg rows; columns are extracted on the host."""
    if sec_type not in _MARGIN_SOURCE:
        return []
    base, est = _MARGIN_SOURCE[sec_type]
    w = MARGIN_RATIO_Z_WINDOW
    mp = MARGIN_RATIO_Z_MIN_PERIODS
    return await conn.fetch(
        f"""
        WITH frame AS (
            SELECT b.code, b.date,
                   CASE WHEN m.rz_buy > 0 AND m.trading_amount > 0
                        THEN m.rz_buy::float8
                             / NULLIF(m.trading_amount::float8, 0)
                   END AS ratio
            FROM {base}
            WHERE b.code IN (
                      SELECT s.code FROM {TABLE_SIGNALS} s
                      WHERE s.sec_type = $1
                        AND s.signal_type = 'margin_ratio'
                        AND NOT EXISTS (
                            SELECT 1 FROM live.live_signals l
                            WHERE l.code = s.code
                              AND l.sec_type = s.sec_type
                              AND l.signal_type = s.signal_type
                              AND l.signal_sub_type = s.signal_sub_type
                              AND l.date = s.date
                              AND l.time = $2
                        )
                  )
              AND b.close IS NOT NULL
              {est}
        ), z0 AS (
            SELECT code, date,
                   (ratio - avg(ratio) OVER w)
                   / NULLIF(stddev_samp(ratio) OVER w, 0) AS z,
                   count(ratio) OVER w AS n
            FROM frame
            WINDOW w AS (
                PARTITION BY code ORDER BY date
                ROWS BETWEEN {w} PRECEDING AND 1 PRECEDING
            )
        ), zed AS (
            SELECT code, date, z FROM z0 WHERE n >= {mp} AND z IS NOT NULL
        )
        SELECT s.code, s.sec_type, s.signal_sub_type, s.date, s.action,
               s.signal_threshold::float8 AS thr,
               ROUND(s.confidence * 100)::int AS confidence,
               z.z::float8 AS zval
        FROM {TABLE_SIGNALS} s
        JOIN zed z ON z.code = s.code AND z.date = s.date
        WHERE s.sec_type = $1 AND s.signal_type = 'margin_ratio'
          AND z.z IS NOT NULL
        {_ANTI_JOIN}
        ORDER BY s.code, s.date
        """,
        sec_type, DAY_CLOSE_TIME,
    )


async def mirror_live_close(conn, sec_type: str) -> int:
    """Mirror the sec_type's not-yet-recorded signal rows into
    live.live_signals as day-close observations (PK upsert — idempotent).

    Returns the number of records written."""
    records = (
        _records_from_rsi(await _fetch_missing_rsi(conn, sec_type))
        + _records_from_gap(await _fetch_missing_gap(conn, sec_type))
        + _records_from_std(await _fetch_missing_std(conn, sec_type))
        + _records_from_pairs(
            await _fetch_missing_pairs(
                conn, sec_type, "mov_pairs",
                "analysis.mov_ave_spreads_detail", "ma5_vs_ma{w}",
                MOV_PAIRS_WINDOWS, "pair_{w}"),
            "mov_pairs", _PAIRS_SUB_TYPES)
        + _records_from_pairs(
            await _fetch_missing_pairs(
                conn, sec_type, "mov_pairs_ema",
                "analysis.mov_ave_spreads_detail_ema", "ema6_vs_ema{w}",
                MOV_PAIRS_EMA_WINDOWS, "ema_pair_{w}"),
            "mov_pairs_ema", _EMA_PAIRS_SUB_TYPES)
        + _records_from_signed_bar(
            await _fetch_missing_pxvol(conn, sec_type), "px_vol")
        + _records_from_signed_bar(
            await _fetch_missing_mratio(conn, sec_type), "margin_ratio")
        + _records_from_hls(await _fetch_missing_hls(conn, sec_type))
    )
    if records:
        await bulk_upsert_async(
            conn, LIVE_SIGNALS_TABLE, records, LIVE_SIGNAL_PK,
        )
    return len(records)
