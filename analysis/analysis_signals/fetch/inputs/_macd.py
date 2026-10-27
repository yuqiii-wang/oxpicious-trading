"""The MACD family's value source (analysis.analysis_signals.fetch.
inputs._macd, 2026-10-26): the PRECOMPUTED typical EMA columns of
stats.{etf,index,stock}_tech_stats (ema12 / ema26 — the classic MACD
spans the builds carry since the 2026-10-26 typical-EMA rebase; the
nearby non-standard span 10 they replaced) LEFT JOINed per (code,
date) onto the base-OHLCV frame (the strategy-tier MacdAlgo's source
verbatim — ONE EMA construction in the repo, never a self-customized
recompute), plus the day-0 ohlcv-boost axes:

    MACD line   = ema12 - ema26                    (read, not recomputed)
    signal line = EMA9 of the MACD line            (inline — the signal
                                                  line is an EMA OF THE
                                                  MACD LINE, so no
                                                  precomputed column
                                                  exists for it)
    histogram   = MACD line - signal line

The signal-line EMA runs through the shared ``compute_emas`` (the
adjust=False recursion, y_0 = x_0 seed — the same convention the
builds use). Rows whose tech_stats join misses (a base row newer than
the last tech_stats build) carry NULL emas → a NULL macd line — no
trigger can fire there (the engine's masks fill before compare), and
the signal-line recursion carries the previous value across them (the
pandas ewm path, the sanctioned NaN fallback).

For ETF the tech_stats EMAs are adjusted-close based and the fetch's
``price`` is the same COALESCE(adj_close, close) — the recorded
histogram-pct normalization stays in one price space.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import rec_cols
from _common.df_utils import compute_emas

from analysis.analysis_signals import config as cfg
from analysis.mov_ave_spread.config import OHL_EST_FILTER, PRICE_SOURCE

from ._shared import (
    PATTERN_TABLE,
    PX_VOL_TABLE,
    amt_ratio_fragments,
    finalize_input_frame,
)

# the macd family's value source: the builds' precomputed typical EMAs
_TECH_STATS_TABLE = "stats.{sec_type}_tech_stats"

# the macd family's FETCHED vocabulary (the ctor shape — the MACD
# construction columns are COMPUTED onto the frame right after, so
# they must NOT be pre-created here: an empty pre-created column would
# collide with the computed one at the rename)
_MACD_FETCH_COLUMNS = ["code", "date", "price", "ema12", "ema26",
                       "pattern", "vol_state", "px_speed"]


async def fetch_latest_macd_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE EMA DATA DATE (max(date) on
    stats.{sec_type}_tech_stats — the macd family's value source) —
    the family's ROLLING LATEST snapshot key (the data-keyed
    idempotence rule: the values come from that table, so its horizon
    is the key)."""
    return await conn.fetchval(
        f"SELECT max(date) FROM {_TECH_STATS_TABLE.format(sec_type=sec_type)}",
    )


async def fetch_macd_inputs(conn, sec_type: str,
                            codes: list[str]) -> pd.DataFrame:
    """The macd family's joined long-format input frame — the base
    OHLCV frame with the PRECOMPUTED typical EMAs (stats.{sec_type}_
    tech_stats.ema12 / ema26, LEFT JOINed — NULL where the tech_stats
    build has no row yet): one row per (code, date), the price in the
    mov_ave_spread convention (the histogram-pct normalization's
    denominator, the same adjusted-close space the etf EMAs live in),
    the day-0 ohlcv-boost axis columns (pattern / vol_state / px_speed,
    int8 codes, AXIS_MISSING where the axis store has no row), and the
    classic MACD construction assembled in-frame (macd_line / macd_
    signal / macd_hist — see the module docstring).

    NO date bounds — each code's FULL history is fetched (the
    signal-line EMA recursion needs every traded row; forward changes
    need post-trigger prices). All numerics cast ::float8, the date
    epoch-converted — the frame the engine receives is
    cudf-representable from construction."""
    if not codes:
        return pd.DataFrame(columns=_MACD_FETCH_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    amt_select, amt_joins = amt_ratio_fragments(sec_type)
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               ts.ema12::float8,
               ts.ema26::float8,
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {amt_select}
        FROM {base}
        LEFT JOIN {_TECH_STATS_TABLE.format(sec_type=sec_type)} ts
               ON ts.code = b.code AND ts.date = b.date
        LEFT JOIN {PATTERN_TABLE} g
               ON g.sec_type = {st_code}
              AND g.code = b.code AND g.date = b.date
        LEFT JOIN {PX_VOL_TABLE} p
               ON p.sec_type = {st_code}
              AND p.code = b.code AND p.date = b.date
        {amt_joins}
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          {ohl_est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes))
    if not rows:
        return pd.DataFrame(columns=_MACD_FETCH_COLUMNS)
    df = pd.DataFrame(
        rec_cols(rows), columns=_MACD_FETCH_COLUMNS + ["amt_ratio"],
    )
    # the classic MACD construction: the lines' EMAs are READ from
    # tech_stats; only the signal line (an EMA of the MACD line — no
    # precomputed column exists) computes inline
    work = df
    work["macd_line"] = work[f"ema{cfg.MACD_EMA_SHORT}"] \
        - work[f"ema{cfg.MACD_EMA_LONG}"]
    work = compute_emas(work, group_key="code", value_col="macd_line",
                        spans=[cfg.MACD_EMA_SIGNAL])
    work = work.rename(
        columns={f"ema{cfg.MACD_EMA_SIGNAL}": "macd_signal"})
    work["macd_hist"] = work["macd_line"] - work["macd_signal"]
    return finalize_input_frame(work)
