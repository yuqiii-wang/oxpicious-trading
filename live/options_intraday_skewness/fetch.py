"""Fetch layer for live.options_intraday_skewness.

Every query is AS-OF BOUNDED: the contract snapshot (terms, strikes, IV,
OI base) is the prev-trading-day ``stats.v_options_quote`` row set
strictly before the target date, and the spot/volume inputs are the
target date's own intraday rows — the on-demand replay for an older date
never peeks at data that postdates it.
"""
from __future__ import annotations

import datetime as _dt
from typing import Final, Optional

import pandas as pd

from live.options_intraday_skewness.config import SKEW_TABLE, SKEW_TYPE

# Underlying-target-type → underlying 5-min spot table (same routing as
# data_viz/api/services/live-data.service.ts and szse-options.service.ts).
SPOT_TABLES: Final[dict[str, str]] = {
    "ETF": "stats.etf_intraday_5min",
    "INDEX": "stats.index_intraday_5min",
}


def _spot_table(target_type: str) -> str:
    t = (target_type or "").strip().upper()
    if t not in SPOT_TABLES:
        raise ValueError(f"unsupported underlying_target_type: {target_type!r}")
    return SPOT_TABLES[t]


async def fetch_option_underlyings(conn) -> list[tuple[str, str]]:
    """(underlying_code, underlying_target_type) of the latest terms
    snapshot — the option universe the pipeline computes series for."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT underlying_code, underlying_target_type
        FROM stats.options_terms
        WHERE date = (SELECT MAX(date) FROM stats.options_terms)
        ORDER BY 1
        """,
    )
    return [(r["underlying_code"], r["underlying_target_type"]) for r in rows]


async def resolve_spot_code(
    conn, target_type: str, underlying: str, trade_date: _dt.date
) -> Optional[str]:
    """Map the options tables' bare underlying code onto the spot table's
    code format: ETF spot rows carry exchange suffixes (510050.SS /
    159915.SZ), index spot rows are bare 6-digit codes."""
    tbl = _spot_table(target_type)
    row = await conn.fetchrow(
        f"""
        SELECT code FROM {tbl}
        WHERE date = $1
          AND code IN ($2, $2 || '.SS', $2 || '.SZ')
        ORDER BY code
        LIMIT 1
        """,
        trade_date, underlying,
    )
    return row["code"] if row else None


async def fetch_latest_spot_date(
    conn, target_type: str, spot_code: str
) -> Optional[_dt.date]:
    """LIVE-mode date resolution: the spot table's latest date for the code."""
    tbl = _spot_table(target_type)
    return await conn.fetchval(
        f"SELECT MAX(date) FROM {tbl} WHERE code = $1", spot_code
    )


async def fetch_spot_max_time(
    conn, target_type: str, spot_code: str, trade_date: _dt.date
) -> Optional[_dt.time]:
    """Latest 5-min bar time for the code-day — the staleness yardstick
    the incremental skip compares the stored series against."""
    tbl = _spot_table(target_type)
    return await conn.fetchval(
        f"SELECT MAX(time) FROM {tbl} WHERE code = $1 AND date = $2",
        spot_code, trade_date,
    )


async def fetch_spot_bars(
    conn, target_type: str, spot_code: str, trade_date: _dt.date
) -> pd.DataFrame:
    """The day's 5-min spot bars (time, close) — S(t) of the skew curves."""
    tbl = _spot_table(target_type)
    rows = await conn.fetch(
        f"""
        SELECT time, close
        FROM {tbl}
        WHERE code = $1 AND date = $2 AND close IS NOT NULL
        ORDER BY time
        """,
        spot_code, trade_date,
    )
    return pd.DataFrame(
        [{"time": r["time"], "close": float(r["close"])} for r in rows]
    )


async def fetch_contracts(
    conn, trade_date: _dt.date
) -> tuple[Optional[_dt.date], pd.DataFrame]:
    """Prev-trading-day contract snapshot — the estimator's OI base AND
    the terms the skew groups by (option_type, expiry_date, strike in
    厘, implied_vol for the validity filter).

    Reads stats.v_options_quote at the latest date STRICTLY BEFORE the
    target date: during a live session the target day's own quote rows
    do not exist yet (the daily build runs after close), and for older
    dates the prev-day snapshot is exactly the position base the
    estimator adds traded volume onto (open_interest of that snapshot =
    oi_base; SSE rows carry 0). Returns (snapshot_date, contracts_df).
    """
    snap_date = await conn.fetchval(
        "SELECT MAX(date) FROM stats.v_options_quote WHERE date < $1::date",
        trade_date,
    )
    if snap_date is None:
        return None, pd.DataFrame()
    rows = await conn.fetch(
        """
        SELECT contract_code, underlying_code, option_type, expiry_date,
               strike_price, implied_vol, open_interest
        FROM stats.v_options_quote
        WHERE date = $1 AND strike_price > 0
        """,
        snap_date,
    )
    df = pd.DataFrame(
        [
            {
                "contract_code": r["contract_code"],
                "underlying_code": r["underlying_code"],
                "option_type": r["option_type"],
                "expiry_date": r["expiry_date"],
                "strike_price": float(r["strike_price"]),
                "implied_vol": (
                    float(r["implied_vol"])
                    if r["implied_vol"] is not None
                    else None
                ),
                "oi_base": (
                    float(r["open_interest"])
                    if r["open_interest"] is not None
                    else 0.0
                ),
            }
            for r in rows
        ]
    )
    return snap_date, df


async def fetch_cum_volumes(
    conn, trade_date: _dt.date
) -> pd.DataFrame:
    """Per-contract day-CUMULATIVE traded volume at each 5-min bar
    (window sum of the streamed bar volumes — the streamer's per-bar
    values are day-cumulative deltas, so they sum back to the source's
    cumulative total). SZSE / CFFEX contracts have no intraday bars and
    are simply absent — their OI stays at the flat daily base."""
    rows = await conn.fetch(
        """
        SELECT contract_code, time,
               SUM(volume) OVER (
                   PARTITION BY contract_code
                   ORDER BY time
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               ) AS cum_vol
        FROM stats.options_intraday_5min
        WHERE date = $1 AND volume IS NOT NULL
        ORDER BY contract_code, time
        """,
        trade_date,
    )
    return pd.DataFrame(
        [
            {
                "contract_code": r["contract_code"],
                "time": r["time"],
                "cum_vol": float(r["cum_vol"]),
            }
            for r in rows
        ]
    )


async def fetch_series_max_time(
    conn, underlying: str, trade_date: _dt.date
) -> Optional[_dt.time]:
    """Latest series time already stored for the underlying-day — used by
    the incremental skip (a day is fresh when the series covers the spot
    bars' latest time)."""
    return await conn.fetchval(
        f"""
        SELECT MAX(time) FROM {SKEW_TABLE}
        WHERE underlying_code = $1 AND date = $2 AND skew_type = $3
        """,
        underlying, trade_date, SKEW_TYPE,
    )
