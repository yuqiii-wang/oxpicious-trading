"""_study_and_select_stocks.py — Study ETF composition (stats.sec_composition)
and select target stocks for streaming.

Consolidates the ETF-membership and target-stock-list logic previously
duplicated across stream_szse_price.py and stream_sse_price.py:

  * ETF_WEIGHT_THRESHOLD — weight_pct cutoff for "in ETF" membership
    (matches build_szse_sse_bse_stocks.py / temp_backfill_is_in_etf.py).
  * TARGET_LOOKBACK_DAYS — date window for the target-list query (see
    load_target_stocks docstring for why the window is needed).
  * load_etf_member_codes(conn) — set of full codes ("600000.SS") in the
    LATEST ETF snapshot. Used by stream_sse_price.py aggregate_bars to set
    is_in_index_or_etf on new stock_identity rows.
  * load_target_stocks(conn, lookback_days) — list of (bare_code, name) for
    SZSE stocks whose latest stock_identity row (within lookback_days) has
    is_in_index_or_etf=TRUE. Used by stream_szse_price.py as its streaming target list.

Both functions are read-only against the database and safe to share across
the two streaming scripts.
"""
from __future__ import annotations

from typing import List, Tuple

import pandas as pd

from downloads._common import setup_logger

logger = setup_logger("study_select_stocks")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A stock "is in an ETF" when its weight in any ETF's active composition
# exceeds this threshold (percent). Matches build_szse_sse_bse_stocks.py
# (ETF_WEIGHT_THRESHOLD) and temp_backfill_is_in_etf.py.
ETF_WEIGHT_THRESHOLD = 0.1

# Lookback window (calendar days) for load_target_stocks. is_in_index_or_etf is
# populated per-date by build_szse_sse_bse_stocks.py from the active ETF
# composition snapshot on or before that date, so filtering stock_identity
# to the last N days is sufficient to identify which stocks are CURRENTLY in
# ETFs — without scanning the full multi-year history (~3M+ rows). 30 days
# covers ~22 trading days and lets Postgres use the
# (exchange, code, date DESC) index efficiently.
TARGET_LOOKBACK_DAYS = 30


# ---------------------------------------------------------------------------
# ETF membership: latest snapshot
# ---------------------------------------------------------------------------
def load_etf_member_codes(conn) -> set:
    """Return the set of full stock codes (e.g. "600000.SS") currently held
    by any ETF, based on the LATEST sec_composition snapshot with
    source_type='etf' and weight_pct > ETF_WEIGHT_THRESHOLD.

    ETF snapshots change quarterly, so this set is valid for the lifetime of a
    streaming session. Used by stream_sse_price.py aggregate_bars to set
    is_in_index_or_etf on new stock_identity rows so they don't default to FALSE
    before the next backfill runs.
    """
    query = """
        WITH latest_snap AS (
            SELECT MAX(snapshot_date) AS snap_date
              FROM stats.sec_composition
             WHERE source_type = 'etf'
        )
        SELECT DISTINCT sc.stock_code
          FROM stats.sec_composition sc
          CROSS JOIN latest_snap ls
         WHERE sc.source_type = 'etf'
           AND sc.snapshot_date = ls.snap_date
           AND sc.weight_pct > %s
           AND sc.stock_code IS NOT NULL
    """
    try:
        with conn.cursor() as cur:
            cur.execute(query, (ETF_WEIGHT_THRESHOLD,))
            return {r[0] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load ETF member codes: %s", e)
        return set()


# ---------------------------------------------------------------------------
# Target stock list: SZSE stocks currently held by ETFs
# ---------------------------------------------------------------------------
def load_target_stocks(
    conn,
    lookback_days: int = TARGET_LOOKBACK_DAYS,
) -> List[Tuple[str, str]]:
    """Return [(bare_code, name), ...] for SZSE stocks currently held by ETFs.

    Uses the pre-computed ``is_in_index_or_etf`` column on stock_identity (populated
    per-date by build_szse_sse_bse_stocks.py from the active ETF composition
    snapshot) and restricts the scan to the last ``lookback_days`` calendar
    days. Without the date filter the query walks the full multi-year history
    of stock_identity (~3M+ rows); the filter narrows it to ~22 trading days,
    letting Postgres serve it from the
    ``idx_stock_identity_exchange_code_date (exchange, code, date DESC)
    INCLUDE (name, is_in_index_or_etf)`` index efficiently.
    """
    query = """
        SELECT DISTINCT ON (si.code) si.code, si.name
          FROM stats.stock_identity si
         WHERE si.exchange = 'SZ'
           AND si.is_in_index_or_etf = TRUE
           AND si.date >= CURRENT_DATE - (%s * INTERVAL '1 day')
         ORDER BY si.code, si.date DESC;
    """
    with conn.cursor() as cur:
        cur.execute(query, (lookback_days,))
        rows = cur.fetchall()
    # Positional whole-column unpack; vectorized bare-code split via pandas
    codes, names = zip(*rows)  # rows are (code, name) tuples
    _df = pd.DataFrame({"code": codes, "name": names})
    bare = _df["code"].str.split(".").str[0]
    return list(zip(bare.tolist(), _df["name"].fillna("").tolist()))


# ---------------------------------------------------------------------------
# Yesterday's top-traded SZSE stocks (by trading_amount)
# ---------------------------------------------------------------------------
def load_yesterday_top_traded_stocks(
    conn,
    n: int = 300,
) -> List[Tuple[str, str]]:
    """Return [(bare_code, name), ...] for the top-N SZSE stocks by
    ``trading_amount`` on the most recent trading day.

    "Yesterday" is resolved as MAX(date) in ``stats.stock_basic_stats``
    scoped to SZSE via the ``stock_identity.exchange`` join (the
    authoritative source of real OHLC trading days — robust to
    weekends/holidays and immune to placeholder rows with 0 amount that
    streaming may insert into ``stock_liquidity_margin`` ahead of the next
    OHLCV build). The trading amount itself is read from
    ``stats.stock_liquidity_margin`` (where trading_shares/trading_amount
    were migrated from stock_basic_stats — see 06_stock_baseline.sql).

    Used by stream_szse_price.py hourly mode: during trading hours the
    streamer samples the top-300 most-traded SZSE stocks each hour instead
    of the full ETF-member list (which is loaded after 15:30).
    """
    query = """
        WITH latest AS (
            SELECT MAX(sbs.date) AS d
              FROM stats.stock_basic_stats sbs
              JOIN stats.stock_identity six
                ON six.code = sbs.code AND six.date = sbs.date
             WHERE six.exchange = 'SZ'
        )
        SELECT slm.code, si.name
          FROM stats.stock_liquidity_margin slm
          JOIN stats.stock_identity si
            ON si.code = slm.code AND si.date = slm.date
           AND si.exchange = 'SZ'
          CROSS JOIN latest l
         WHERE slm.date = l.d
           AND slm.trading_amount > 0
         ORDER BY slm.trading_amount DESC
         LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(query, (n,))
        rows = cur.fetchall()
    # Positional whole-column unpack; vectorized bare-code split via pandas
    codes, names = zip(*rows)  # rows are (code, name) tuples
    _df = pd.DataFrame({"code": codes, "name": names})
    bare = _df["code"].str.split(".").str[0]
    return list(zip(bare.tolist(), _df["name"].fillna("").tolist()))
