"""DB reads for the 1-month forecast.

Three concerns:
  1. fetch_run_end_state() — read a strategy run's end-of-backtest position
     (the position that WOULD have been carried past end_date had the run
     not force-liquidated on the last day). This is total_qty_before of the
     FINAL LIQUIDATION SELL. Also fetches the last total_pnl from
     strategy_daily (the P&L offset for the forecast).
  2. fetch_last_ohlc() — the trailing OHLC + trading_amount for the 20d
     window (mirror/flip source + sigma_20d + gap/amt stats) and the 255d
     window (sigma_255d for the std ratio scale).
  3. fetch_current_rsi() — the RSI(6/10/14/20) row at forecast_date.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import datetime

from strategy._common.constants import SEC_TYPE_BASIC_STATS_TABLE
from strategy._1m_forcast.constants import HORIZON_DAYS, LONG_TERM_DAYS, MIN_HISTORY_CLOSSES

# Marker written by the engine's final-liquidation block (engine.py).
FINAL_LIQ_REASON_PREFIX = "FINAL LIQUIDATION"


# ---------------------------------------------------------------------------
# Run end-state — the position carried into the forecast horizon
# ---------------------------------------------------------------------------
_RUN_STATE_SQL = """
    SELECT
        s.seq_id,
        s.sec_type,
        s.code,
        s.end_date,
        i.first_buy_fill_price
    FROM strategy.strategy_identity s
    JOIN strategy.strategy_results i ON i.seq_id = s.seq_id
    WHERE s.seq_id = $1
"""


async def fetch_run_end_state(
    conn, seq_id: int,
) -> Optional[Dict[str, Any]]:
    """Read the run's end-of-backtest state for forecasting.

    Returns None when the run has no open position to forecast (total_qty
    already 0 before the last day, or the run has no decisions).

    The returned dict carries:
      - seq_id, sec_type, code, forecast_date (the run's LAST DATA date —
        strategy_identity.end_date, e.g. yesterday; NOT the last decision's
        exec_date — the position is typically held after the last trade),
        first_buy_fill_price
      - total_qty: position carried into the horizon
        (= total_qty_before of the FINAL LIQUIDATION SELL when the last
        decision is the force-liquidation; else total_qty_after of the last
        decision)
      - cost_basis_norm: weighted-avg BUY normalized price at horizon start
        (= normalized_mean_buy_price of the same decision)
      - is_final_liquidation: whether the last decision was the force-liquidation
      - last_total_pnl: the final total_pnl from strategy_daily (P&L offset)
    """
    row = await conn.fetchrow(_RUN_STATE_SQL, seq_id)
    if row is None:
        return None

    last = await conn.fetchrow(
        "SELECT side, total_qty_before, total_qty_after, "
        "       normalized_mean_buy_price, signal_reason, exec_date "
        "FROM strategy.trade_decision "
        "WHERE seq_id = $1 AND signal_reason NOT LIKE 'FORECAST SELL%' "
        "ORDER BY decision_no DESC LIMIT 1",
        seq_id,
    )
    if last is None:
        return None

    is_final_liq = (
        last["signal_reason"] is not None
        and last["signal_reason"].startswith(FINAL_LIQ_REASON_PREFIX)
        and last["side"] == "SELL"
    )
    if is_final_liq:
        total_qty = float(last["total_qty_before"] or 0.0)
    else:
        total_qty = float(last["total_qty_after"] or 0.0)
    cost_basis = float(last["normalized_mean_buy_price"] or 0.0)

    if total_qty <= 0:
        return None  # nothing to forecast — no open position

    # Anchor the forecast at the run's LAST DATA date (end_date = the last
    # OHLC day the backtest consumed, e.g. yesterday), NOT the last
    # decision's exec_date — the position is typically held for days/weeks
    # after the last trade, and the forecast must project forward from the
    # latest data (matching the non-forecast view which shows data through
    # end_date). Falls back to the last decision date when end_date is NULL.
    forecast_date = row["end_date"] or last["exec_date"]

    # Fetch the last total_pnl from strategy_daily (the P&L forecast offset)
    # at the anchor date — includes MTM changes after the last decision.
    last_pnl_row = await conn.fetchrow(
        "SELECT total_pnl FROM strategy.strategy_daily "
        "WHERE seq_id = $1 AND trade_date <= $2 "
        "ORDER BY trade_date DESC LIMIT 1",
        seq_id, forecast_date,
    )
    last_total_pnl = float(last_pnl_row["total_pnl"]) if last_pnl_row else 0.0

    return {
        "seq_id": seq_id,
        "sec_type": row["sec_type"],
        "code": row["code"],
        "forecast_date": forecast_date,
        "first_buy_fill_price": (
            float(row["first_buy_fill_price"])
            if row["first_buy_fill_price"] is not None else None
        ),
        "total_qty": total_qty,
        "cost_basis_norm": cost_basis,
        "is_final_liquidation": is_final_liq,
        "last_total_pnl": last_total_pnl,
    }


# ---------------------------------------------------------------------------
# Trailing OHLC + trading_amount — for sigma + OHLC gap / amt stats + mirror/flip
# ---------------------------------------------------------------------------
# Fetch HORIZON_DAYS+1 OHLC rows so we get HORIZON_DAYS daily returns. If
# fewer rows exist, asyncpg just returns what's available (down to
# MIN_HISTORY_CLOSSES). trading_amount is only on index_basic_stats (etf /
# stock basic_stats have no amount column); the query selects it conditionally
# per sec_type so etf/stock runs just get None.
#
# Also fetch LONG_TERM_DAYS+1 rows for sigma_255d (the std ratio scale).
# The 255d fetch skips trading_amount (not needed, saves bandwidth).


def _build_ohlc_sql(sec_type: str, with_amt: bool) -> str:
    """Build the trailing-OHLC query for the given sec_type's basic_stats table.

    trading_amount is only present on index_basic_stats; for etf/stock we
    select NULL AS trading_amount (the column doesn't exist there).
    """
    table = SEC_TYPE_BASIC_STATS_TABLE[sec_type]
    if with_amt:
        amt_col = "trading_amount" if sec_type == "index" else "NULL::numeric"
        amt_select = f"{amt_col} AS trading_amount"
    else:
        amt_select = "NULL::numeric AS trading_amount"
    return f"""
        SELECT date,
               open  AS open_price,
               high  AS high_price,
               low   AS low_price,
               close AS close_price,
               {amt_select}
        FROM {table}
        WHERE code = $1 AND date <= $2
          AND close IS NOT NULL AND close > 0
          AND open  IS NOT NULL AND open  > 0
          AND high  IS NOT NULL AND high  > 0
          AND low   IS NOT NULL AND low   > 0
        ORDER BY date DESC
        LIMIT $3
    """


async def fetch_last_ohlc(
    conn,
    sec_type: str,
    code: str,
    end_date: datetime.date,
    limit: int = HORIZON_DAYS + 1,
    with_amt: bool = True,
) -> List[Dict[str, Any]]:
    """Return the trailing OHLC + trading_amount ending on (or before) end_date,
    ascending by date.

    Fetches up to ``limit`` rows. Returns ``[]`` if fewer than
    MIN_HISTORY_CLOSSES rows are available (the caller then skips the forecast).

    Each row: {date, open, high, low, close, trading_amount}. trading_amount
    is None for etf/stock (no such column) or when with_amt=False.
    """
    rows = await conn.fetch(
        _build_ohlc_sql(sec_type, with_amt), code, end_date, limit,
    )
    if len(rows) < MIN_HISTORY_CLOSSES:
        return []
    out: List[Dict[str, Any]] = []
    for r in reversed(rows):  # fetched DESC; reverse to ascending
        out.append({
            "date": r["date"],
            "open": float(r["open_price"]),
            "high": float(r["high_price"]),
            "low": float(r["low_price"]),
            "close": float(r["close_price"]),
            "trading_amount": (
                float(r["trading_amount"])
                if r["trading_amount"] is not None else None
            ),
        })
    return out


# Lookback for the rolling 255d std max: LONG_TERM_DAYS (for the std window)
# + ~252 trading days (1 calendar year of rolling windows).
ROLLING_MAX_LOOKBACK = LONG_TERM_DAYS + 252 + 10


async def fetch_255d_ohlc(
    conn,
    sec_type: str,
    code: str,
    end_date: datetime.date,
) -> List[Dict[str, Any]]:
    """Return trailing OHLC (no trading_amount) for sigma_255d + rolling max.

    Fetches up to ROLLING_MAX_LOOKBACK rows so the caller can compute both
    the current 255d std AND the max 255d std over the past year (rolling
    255d std for each of the past ~252 trading days). Returns [] if < 2 rows.
    """
    return await fetch_last_ohlc(
        conn, sec_type, code, end_date,
        limit=ROLLING_MAX_LOOKBACK,
        with_amt=False,
    )


# ---------------------------------------------------------------------------
# Current RSI — from analysis.mov_ave_rsi
# ---------------------------------------------------------------------------
_RSI_SQL = """
    SELECT rsi_6days, rsi_10days, rsi_14days, rsi_20days
    FROM analysis.mov_ave_rsi
    WHERE sec_type = $1 AND code = $2 AND date <= $3
    ORDER BY date DESC LIMIT 1
"""


async def fetch_current_rsi(
    conn,
    sec_type: str,
    code: str,
    end_date: datetime.date,
) -> Optional[Dict[str, Optional[float]]]:
    """RSI(6/10/14/20) at or before forecast_date. None if no RSI history."""
    row = await conn.fetchrow(_RSI_SQL, sec_type, code, end_date)
    if row is None:
        return None
    return {
        "rsi_6": float(row["rsi_6days"]) if row["rsi_6days"] is not None else None,
        "rsi_10": float(row["rsi_10days"]) if row["rsi_10days"] is not None else None,
        "rsi_14": float(row["rsi_14days"]) if row["rsi_14days"] is not None else None,
        "rsi_20": float(row["rsi_20days"]) if row["rsi_20days"] is not None else None,
    }


# ---------------------------------------------------------------------------
# Discover seq_ids to forecast — list (seq_id, code) pairs for a strategy
# ---------------------------------------------------------------------------
async def fetch_strategy_seqs(
    conn,
    strategy_name: str,
    sec_type: str,
    codes: Optional[list] = None,
    skip_existing: bool = False,
) -> List[tuple]:
    """Return [(seq_id, code), ...] for the given strategy + sec_type,
    optionally filtered by a code list. With ``skip_existing=True``, seq_ids
    that already have forecast rows are excluded (honors the "only missing
    data" convention; the forecast is fixed per immutable run).
    """
    skip_sql = ""
    if skip_existing:
        skip_sql = " AND s.seq_id NOT IN (SELECT DISTINCT seq_id FROM strategy.forecast_1m)"

    # Only forecast PARENT seqs (parent_seq_id IS NULL). Child seqs are
    # created by this module and should NOT be re-forecast (avoids
    # grandchildren).
    parent_filter = " AND s.parent_seq_id IS NULL"

    if codes:
        rows = await conn.fetch(
            f"SELECT s.seq_id, s.code "
            "FROM strategy.strategy_identity s "
            "WHERE s.strategy_name = $1 AND s.sec_type = $2 "
            "  AND s.code = ANY($3::text[])"
            f"{skip_sql}{parent_filter} "
            "ORDER BY s.seq_id, s.code",
            strategy_name, sec_type, sorted(codes),
        )
    else:
        rows = await conn.fetch(
            f"SELECT s.seq_id, s.code "
            "FROM strategy.strategy_identity s "
            "WHERE s.strategy_name = $1 AND s.sec_type = $2"
            f"{skip_sql}{parent_filter} "
            "ORDER BY s.seq_id, s.code",
            strategy_name, sec_type,
        )
    return [(r["seq_id"], r["code"]) for r in rows]
