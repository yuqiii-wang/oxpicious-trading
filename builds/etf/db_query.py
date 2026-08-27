"""DB query helpers for builds.etf (existing OHLCV + margin retrieval)."""
import pandas as pd

from _common.build_commons import rec_col, rec_cols
from _common.df_utils import safe_columns


async def query_existing_ohlcv_margin_from_db(conn, verbose=True, code=None):
    """Query existing OHLCV + margin data from the database.

    Returns (ohlcv_df, margin_df) with the same column schemas as
    build_ohlcv_df() and build_margin_df() so they can be concatenated
    with new (missing-date) source data before applying split/MA.

    When *code* is set (canonical "NNNNNN.SZ/.SS"), only that code's rows
    are fetched — split adjustment and MAs are per-code, so a --code build
    never needs other ETFs' history.
    """
    if verbose:
        scope = f" for {code}" if code else ""
        print(f"    [DB] Querying existing OHLCV + margin from database{scope} …", flush=True)

    if code is not None:
        rows = await conn.fetch("""
            SELECT
                i.date, i.code, i.name, i.exchange,
                b.prev_close, b.open, b.high, b.low, b.close, b.pct_change,
                COALESCE(l.trading_shares, 0) AS trading_shares,
                COALESCE(l.trading_amount, 0) AS trading_amount,
                COALESCE(l.rz_buy, 0)       AS rz_buy,
                COALESCE(l.rz_balance, 0)   AS rz_balance,
                COALESCE(l.rq_sell_qty, 0)  AS rq_sell_qty,
                COALESCE(l.rq_balance_qty, 0) AS rq_balance_qty,
                COALESCE(l.rq_balance_amt, 0) AS rq_balance_amt,
                COALESCE(l.total_balance, 0) AS total_balance
            FROM stats.etf_identity i
            JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
            LEFT JOIN stats.etf_liquidity_margin l ON l.date = i.date AND l.code = i.code
            WHERE i.code = $1
            ORDER BY i.code, i.date
        """, code)
    else:
        rows = await conn.fetch("""
            SELECT
                i.date, i.code, i.name, i.exchange,
                b.prev_close, b.open, b.high, b.low, b.close, b.pct_change,
                COALESCE(l.trading_shares, 0) AS trading_shares,
                COALESCE(l.trading_amount, 0) AS trading_amount,
                COALESCE(l.rz_buy, 0)       AS rz_buy,
                COALESCE(l.rz_balance, 0)   AS rz_balance,
                COALESCE(l.rq_sell_qty, 0)  AS rq_sell_qty,
                COALESCE(l.rq_balance_qty, 0) AS rq_balance_qty,
                COALESCE(l.rq_balance_amt, 0) AS rq_balance_amt,
                COALESCE(l.total_balance, 0) AS total_balance
            FROM stats.etf_identity i
            JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
            LEFT JOIN stats.etf_liquidity_margin l ON l.date = i.date AND l.code = i.code
            ORDER BY i.code, i.date
        """)

    if not rows:
        if verbose:
            print("    [DB] No existing OHLCV data found", flush=True)
        return pd.DataFrame(), pd.DataFrame()

    # Whole-column extraction (rec_cols: one positional-unpack pass)
    df = pd.DataFrame(rec_cols(rows))
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[us]")
    df_cols = safe_columns(df)
    for _nc in ["prev_close", "open", "high", "low", "close", "pct_change",
                "trading_shares", "trading_amount", "rz_buy", "rz_balance",
                "rq_sell_qty", "rq_balance_qty", "rq_balance_amt",
                "total_balance"]:
        if _nc in df_cols:
            df[_nc] = pd.to_numeric(df[_nc], errors="coerce")

    ohlcv_cols = ["date", "code", "name", "exchange", "prev_close", "open",
                  "high", "low", "close", "pct_change", "trading_shares",
                  "trading_amount"]
    margin_cols = ["date", "code", "rz_buy", "rz_balance", "rq_sell_qty",
                   "rq_balance_qty", "rq_balance_amt", "total_balance"]

    ohlcv_df = df[ohlcv_cols].copy()

    margin_df = df[margin_cols].copy()
    margin_mask = (
        (margin_df["rz_balance"] > 0) |
        (margin_df["rq_balance_qty"] > 0) |
        (margin_df["total_balance"] > 0)
    )
    margin_df = margin_df[margin_mask].reset_index(drop=True)

    if verbose:
        n_codes = ohlcv_df["code"].nunique()
        n_dates = ohlcv_df["date"].dt.strftime("%Y-%m-%d").nunique()
        d0 = ohlcv_df["date"].min().date()
        d1 = ohlcv_df["date"].max().date()
        print(f"    [DB] OHLCV: {len(ohlcv_df):,} rows | {n_codes} codes | "
              f"{n_dates} dates | {d0} → {d1}", flush=True)
        print(f"    [DB] Margin: {len(margin_df):,} rows with margin activity", flush=True)

    return ohlcv_df, margin_df


async def fetch_latest_etf_composition(conn, etf_codes=None):
    """Fetch the LATEST composition snapshot per ETF from stats.sec_composition.

    Uses temporal extrapolation (latest snapshot applied to all dates),
    mirroring the index dividend_yield pattern in analyze.pe_and_dividends.

    Returns DataFrame with columns: etf_code, stock_code, weight_fraction.
    """
    if etf_codes is not None and not etf_codes:
        return pd.DataFrame(columns=["etf_code", "stock_code", "weight_fraction"])

    code_filter = ""
    params = []
    if etf_codes is not None:
        code_filter = "AND sc.code = ANY($1::text[])"
        params = [sorted(etf_codes)]

    rows = await conn.fetch(f"""
        WITH latest AS (
            SELECT code, MAX(snapshot_date) AS max_date
            FROM stats.sec_composition
            WHERE source_type = 'etf'
              AND stock_code IS NOT NULL
              {code_filter}
            GROUP BY code
        )
        SELECT
            sc.code       AS etf_code,
            sc.stock_code,
            sc.weight_pct
        FROM stats.sec_composition sc
        JOIN latest ld
            ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
        WHERE sc.source_type = 'etf'
          AND sc.stock_code IS NOT NULL
          AND sc.weight_pct > 0
    """, *params)

    if not rows:
        return pd.DataFrame(columns=["etf_code", "stock_code", "weight_fraction"])
    df = pd.DataFrame(rec_cols(rows))
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    df = df.dropna(subset=["stock_code", "weight_pct"])
    df["weight_fraction"] = df["weight_pct"] / 100.0
    return df[["etf_code", "stock_code", "weight_fraction"]]
