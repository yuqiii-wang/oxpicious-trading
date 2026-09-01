"""DB query helpers for builds.etf (existing OHLCV + margin retrieval)."""
import datetime

import pandas as pd

from _common.build_commons import rec_col, rec_cols
from _common.df_utils import epoch_col_to_dt64, host_array

# B3 trailing-window fetch: the market-wide DB pull exists to give MA/EMA
# and corp-action adjustment their trailing context for the new rows. Only
# that context is needed — not the full 1.02M-row history:
#   * ma255 needs the 255 trading rows before each new row (~390 calendar
#     days);
#   * ema255 (adjust=False, alpha=2/256) residual seed weight after ~740
#     trading rows is e^(-2·740/256) ≈ 0.3% — converged;
#   * corp-action continuity is restored exactly by adj_seeds (the stored
#     cum_split_factor / cum_dividend_per_share of the last pre-cutoff row
#     per code — cum factors are forward products, so seeding the window
#     start reproduces full-history values).
# 1100 calendar days ≈ 740 trading days, satisfying all three.
DB_TAIL_DAYS: int = 1100

# All numeric columns are cast to float8 IN SQL: asyncpg returns NUMERIC
# as Python Decimals → object-dtype frame columns whose pd.to_numeric /
# cudf conversions fall back per column ("Unrecognized datatype"). float8
# lands as native float64 — final dtypes assigned at the source, no
# post-parse numeric conversion needed (one-pass dtype contract).
_SELECT_COLS = """
    SELECT
        extract(epoch from i.date)::float8 AS date,
        i.code, i.name, i.exchange,
        b.prev_close::float8 AS prev_close,
        b.open::float8       AS open,
        b.high::float8       AS high,
        b.low::float8        AS low,
        b.close::float8      AS close,
        b.pct_change::float8 AS pct_change,
        COALESCE(l.trading_shares, 0)::float8 AS trading_shares,
        COALESCE(l.trading_amount, 0)::float8 AS trading_amount,
        COALESCE(l.rz_buy, 0)::float8       AS rz_buy,
        COALESCE(l.rz_balance, 0)::float8   AS rz_balance,
        COALESCE(l.rq_sell_qty, 0)::float8  AS rq_sell_qty,
        COALESCE(l.rq_balance_qty, 0)::float8 AS rq_balance_qty,
        COALESCE(l.rq_balance_amt, 0)::float8 AS rq_balance_amt,
        COALESCE(l.total_balance, 0)::float8  AS total_balance
    FROM stats.etf_identity i
    JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
    LEFT JOIN stats.etf_liquidity_margin l ON l.date = i.date AND l.code = i.code
"""

# Same shape as _SELECT_COLS + the stored adjustment state, restricted to
# the LAST row before the cutoff per code (DISTINCT ON). Those rows are
# concatenated in front of the window so the first in-window row keeps its
# true predecessor close (corp-action detection is close-to-close), and
# their stored cum factor/dividend seed the window's forward products.
_SELECT_SEED_COLS = """
    SELECT DISTINCT ON (i.code)
        extract(epoch from i.date)::float8 AS date,
        i.code, i.name, i.exchange,
        b.prev_close::float8 AS prev_close,
        b.open::float8       AS open,
        b.high::float8       AS high,
        b.low::float8        AS low,
        b.close::float8      AS close,
        b.pct_change::float8 AS pct_change,
        COALESCE(l.trading_shares, 0)::float8 AS trading_shares,
        COALESCE(l.trading_amount, 0)::float8 AS trading_amount,
        COALESCE(l.rz_buy, 0)::float8       AS rz_buy,
        COALESCE(l.rz_balance, 0)::float8   AS rz_balance,
        COALESCE(l.rq_sell_qty, 0)::float8  AS rq_sell_qty,
        COALESCE(l.rq_balance_qty, 0)::float8 AS rq_balance_qty,
        COALESCE(l.rq_balance_amt, 0)::float8 AS rq_balance_amt,
        COALESCE(l.total_balance, 0)::float8  AS total_balance,
        a.cum_split_factor::float8      AS cum_split_factor,
        a.cum_dividend_per_share::float8 AS cum_dividend_per_share
    FROM stats.etf_identity i
    JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
    LEFT JOIN stats.etf_liquidity_margin l ON l.date = i.date AND l.code = i.code
    LEFT JOIN stats.etf_adjustment a ON a.date = i.date AND a.code = i.code
    WHERE i.date < $1
    ORDER BY i.code, i.date DESC
"""

async def query_existing_ohlcv_margin_from_db(conn, verbose=True, code=None):
    """Query existing OHLCV + margin data from the database.

    Returns (ohlcv_df, margin_df, adj_seeds) with the same column schemas as
    build_ohlcv_df() and build_margin_df() so they can be concatenated
    with new (missing-date) source data before applying split/MA.

    Market-wide pulls are window-truncated to the last DB_TAIL_DAYS
    calendar days (B3); *adj_seeds* (code, cum_factor, cum_dividend) then
    restores corp-action continuity across the cutoff — pass it through to
    prepare_features → apply_split_adjustment. The single-code pull keeps
    the code's FULL history (small) and returns adj_seeds=None.

    When *code* is set (canonical "NNNNNN.SZ/.SS"), only that code's rows
    are fetched — split adjustment and MAs are per-code, so a --code build
    never needs other ETFs' history.
    """
    if verbose:
        scope = f" for {code}" if code else ""
        print(f"    [DB] Querying existing OHLCV + margin from database{scope} …", flush=True)

    adj_seeds: pd.DataFrame | None = None
    if code is not None:
        rows = await conn.fetch(_SELECT_COLS + " WHERE i.code = $1", code)
    else:
        max_rows = await conn.fetch("SELECT MAX(date) AS max_date FROM stats.etf_identity")
        max_d = max_rows[0]["max_date"] if max_rows else None
        if max_d is None:
            if verbose:
                print("    [DB] No existing OHLCV data found", flush=True)
            return pd.DataFrame(), pd.DataFrame(), None
        cutoff = max_d - datetime.timedelta(days=DB_TAIL_DAYS)
        rows = await conn.fetch(
            _SELECT_COLS + " WHERE i.date >= $1 ORDER BY i.code, i.date", cutoff)
        seed_rows = await conn.fetch(_SELECT_SEED_COLS, cutoff)
        if seed_rows:
            seed_df = pd.DataFrame(rec_cols(seed_rows))
            seed_df["date"] = epoch_col_to_dt64(
                seed_df["date"], index=seed_df.index)
            # adj_seeds: stored forward-product state at the cutoff row —
            # whole-column extraction, no per-row iteration. float8 casts in
            # SQL land as float64; only the LEFT-JOIN NULLs need filling.
            adj_seeds = seed_df[["code", "cum_split_factor", "cum_dividend_per_share"]] \
                .rename(columns={"cum_split_factor": "cum_factor",
                                 "cum_dividend_per_share": "cum_dividend"})
            adj_seeds["cum_factor"] = adj_seeds["cum_factor"].fillna(1.0)
            adj_seeds["cum_dividend"] = adj_seeds["cum_dividend"].fillna(0.0)
            seed_df = seed_df.drop(columns=["cum_split_factor", "cum_dividend_per_share"])
            if verbose:
                print(f"    [DB] Trailing window ≥ {cutoff} ({DB_TAIL_DAYS}d): "
                      f"{len(rows):,} window rows + {len(seed_df):,} seed rows", flush=True)
            # Seed rows in FRONT of the window (chronological per code);
            # concat aligns by column name (the two SELECTs share 18 cols).
            # Both sides share identical dtypes (datetime64[us] dates +
            # float64 numerics straight from the SQL casts) — no cudf
            # concat dtype-mismatch fallback.
            window_df = pd.DataFrame(rec_cols(rows))
            window_df["date"] = epoch_col_to_dt64(
                window_df["date"], index=window_df.index)
            df = pd.concat([seed_df, window_df], ignore_index=True)
        else:
            df = pd.DataFrame(rec_cols(rows))
            df["date"] = epoch_col_to_dt64(df["date"], index=df.index)

    if not len(df):
        if verbose:
            print("    [DB] No existing OHLCV data found", flush=True)
        return pd.DataFrame(), pd.DataFrame(), adj_seeds

    ohlcv_cols = ["date", "code", "name", "exchange", "prev_close", "open",
                  "high", "low", "close", "pct_change", "trading_shares",
                  "trading_amount"]
    margin_cols = ["date", "code", "rz_buy", "rz_balance", "rq_sell_qty",
                   "rq_balance_qty", "rq_balance_amt", "total_balance"]

    ohlcv_df = df[ohlcv_cols]
    margin_df = df[margin_cols]
    margin_mask = (
        (margin_df["rz_balance"] > 0) |
        (margin_df["rq_balance_qty"] > 0) |
        (margin_df["total_balance"] > 0)
    )
    # boolean-mask result is a fresh frame; downstream combine/sort is
    # index-blind — no reindex
    margin_df = margin_df[margin_mask]

    if verbose:
        n_codes = ohlcv_df["code"].nunique()
        n_dates = ohlcv_df["date"].dt.strftime("%Y-%m-%d").nunique()
        # host unwrap ONCE (Timestamp.date has no cudf fast path)
        d0 = str(host_array(ohlcv_df["date"].min()).astype("datetime64[D]"))
        d1 = str(host_array(ohlcv_df["date"].max()).astype("datetime64[D]"))
        print(f"    [DB] OHLCV: {len(ohlcv_df):,} rows | {n_codes} codes | "
              f"{n_dates} dates | {d0} → {d1}", flush=True)
        print(f"    [DB] Margin: {len(margin_df):,} rows with margin activity", flush=True)

    return ohlcv_df, margin_df, adj_seeds


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
            sc.weight_pct::float8 AS weight_pct
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
    df = df.dropna(subset=["stock_code", "weight_pct"])
    df["weight_fraction"] = df["weight_pct"] / 100.0
    return df[["etf_code", "stock_code", "weight_fraction"]]
