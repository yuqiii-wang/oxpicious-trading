"""Entry point for analyze.pe_and_dividends.

Run via ``python -m analyze.pe_and_dividends``.

Pipeline (default: incremental — skip dates already in the DB;
``--force``: truncate-then-recompute on every run).

Per sec_type (index / etf / stock):
  1. Fetch active-universe codes from the identity table.
  2. Fetch source data:
     - index: PE (index_valuation) + close (index_basic_stats) + latest
       composition (sec_composition) + constituent stock dividends
       (stock_dividends).
     - etf: close (etf_basic_stats) + implied_dividend_per_share
       (etf_adjustment).
     - stock: close (stock_basic_stats) + dividends (stock_dividends).
  3. Compute pe (raw, invalid values masked) and dividend_yield
     (trailing-12m D/P) on FULL history (trailing-12m DPS + 5y rolling
     windows need it).
  4. Write daily rows to analysis.pe + analysis.dividends (2026-09 split):
     - ``--force``: DELETE sec_type rows + COPY-insert.
     - default: upsert ONLY rows whose date is in the missing-dates set.
  5. Compute monthly 5y rolling stats. Write to
     analysis.pe_and_dividend_stats:
     - ``--force``: DELETE sec_type rows + COPY-insert.
     - default: if new month-end dates are missing, DELETE sec_type rows
       + recompute (is_active flag + 5y rolling windows require full
       recompute when a new month appears). If no missing month-end
       dates, skip stats entirely.
  6. Compute monthly trailing percentile BANDS of pe / dividend_yield
     (analysis.pe_and_dividend_pct — internal step pct_bands.py):
     ``--force`` / ``--code`` DELETE the scope + rebuild; incremental
     computes only missing (code, month, metric) triples (trailing
     windows make completed months immutable).
  7. Compute band-BREAK excursion streaks of pe / dividend_yield against
     those bands (analysis.pe_and_dividend_pct_streaks — internal step
     pct_streaks.py): episodes shift with new data, so the scope is
     rebuilt WHOLESALE per sec_type (per code in --code mode) on every
     run that processes it.
  8. Upsert analysis_identity.

Incremental mode rationale
  The pe and dividend_yield for past dates don't change retroactively
  (PE and dividends are historical facts), so existing rows are valid.
  New dates get appended via upsert. The monthly stats table has an
  is_active flag that flips when a new month-end appears, so stats is
  recomputed per sec_type only when new month-end dates are detected
  (otherwise skipped).
"""
from __future__ import annotations

import sys

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas-importing modules below.
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.build_commons import (
    find_missing_analysis_dates,
)
from _common.db_commons import (
    copy_or_upsert_split_async,
    copy_insert_async,
    copy_frame_chunked_async,
)

import pandas as pd

from analyze._common.sanitize import sanitize_for_db_insert
from _common.df_utils.sanitize import safe_columns
from analyze.pe_and_dividends.config import (
    ANALYSIS_NAME_PE,
    ANALYSIS_NAME_DIVIDENDS,
    PE_TABLE,
    DIVIDENDS_TABLE,
    STATS_TABLE,
    DESCRIPTION_PE,
    DESCRIPTION_DIVIDENDS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)
from analyze.pe_and_dividends.fetch import (
    fetch_active_codes,
    fetch_index_pe_and_close,
    fetch_latest_index_composition,
    fetch_stock_dividends,
    fetch_constituent_closes,
    fetch_etf_close_and_dividends,
    fetch_stock_close,
    fetch_trading_dates,
)
from analyze.pe_and_dividends.compute import (
    clean_pe,
    compute_trailing_12m_dps,
    compute_index_dividend_yield,
    compute_simple_dividend_yield,
    build_detail_rows,
    compute_monthly_stats,
    find_month_end_dates,
)
from analyze.pe_and_dividends.pct_bands import run_pd_pct_bands
from analyze.pe_and_dividends.pct_streaks import run_pd_pct_streaks

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis

logger = setup_logging("pe_and_dividends")


def _normalize_stock_codes(df, col: str) -> None:
    """Normalize a stock-code column in-place by stripping exchange suffixes.

    Vectorized str ops (two kernels total) — replaces the former per-row
    .apply, which paid one proxy Series.__getitem__ fallback per cell."""
    if col in safe_columns(df):
        df[col] = df[col].str.upper().str.replace(
            r"\.(SS|SZ|SH|BJ|HK)$", "", regex=True
        )


async def _process_index(
    conn, pool, *, force: bool, target_dates_pe: set | None,
    target_dates_dy: set | None, target_dates_stats: set | None,
    code: str | None = None,
) -> int:
    """Process sec_type='index' end-to-end.

    ``code`` (single-code mode, --code): bypasses the active-universe
    pre-filter and recomputes ALL rows for that one code (upsert).
    """
    st = "index"
    if code is not None:
        code_list = [code]
        logger.info(f"\n  [{st}] SINGLE-CODE mode: processing {code}")
    else:
        logger.info(f"\n  [{st}] Fetching active codes...")
        codes = await fetch_active_codes(conn, st)
        code_list = sorted(codes)
        logger.info(f"  [{st}]   {len(code_list):,} active codes")
        if not code_list:
            logger.info(f"  [{st}]   no active codes; skipping.")
            return 0

    # ---- Fetch source data ----------------------------------------------
    logger.info(f"  [{st}] Fetching PE + close from index_valuation + index_basic_stats...")
    close_df = await fetch_index_pe_and_close(conn, code_list)
    logger.info(f"  [{st}]   {len(close_df):,} (code, date) rows with close")

    if code is not None and close_df.empty:
        logger.info(f"  [{st}]   no source data for {code} in {st}; skipping.")
        return 0

    logger.info(f"  [{st}] Fetching latest composition from sec_composition...")
    comp_df = await fetch_latest_index_composition(conn, code_list)
    logger.info(f"  [{st}]   {len(comp_df):,} (index_code, stock_code) composition rows")

    # Get unique constituent stock codes (original format, before normalization)
    if not comp_df.empty:
        constituent_codes = sorted(comp_df["stock_code"].unique().tolist())
    else:
        constituent_codes = []
    logger.info(f"  [{st}]   {len(constituent_codes):,} unique constituent stocks")

    # Fetch ALL stock dividends (small table ~12K rows) — avoids code-format
    # mismatch issues between sec_composition.stock_code and
    # stock_dividends.code (mixed suffix conventions).
    logger.info(f"  [{st}] Fetching ALL stock dividends...")
    div_df = await fetch_stock_dividends(conn, stock_codes=None)
    logger.info(f"  [{st}]   {len(div_df):,} dividend events total")

    # Normalize stock codes for cross-table join: strip exchange suffixes
    # from both composition.stock_code and dividends.code so they match
    # regardless of suffix convention.
    _normalize_stock_codes(comp_df, "stock_code")
    _normalize_stock_codes(div_df, "code")

    logger.info(f"  [{st}] Fetching trading dates...")
    trading_dates = await fetch_trading_dates(conn, st)
    logger.info(f"  [{st}]   {len(trading_dates):,} trading dates")

    # Constituent closes (per-share denominators for the cap-weighted
    # constituent-yield aggregation — see compute_index_dividend_yield).
    logger.info(f"  [{st}] Fetching constituent closes from stock_basic_stats...")
    stock_close_df = await fetch_constituent_closes(conn, constituent_codes)
    logger.info(f"  [{st}]   {len(stock_close_df):,} (code, date) constituent close rows")

    # ---- Compute pe --------------------------------------------------------
    logger.info(f"  [{st}] Cleaning pe (masking invalid <= 0 / NULL values)...")
    pe = clean_pe(close_df)
    logger.info(f"  [{st}]   {pe.notna().sum():,} non-null pe values")

    # ---- Compute dividend_yield ------------------------------------------
    logger.info(f"  [{st}] Computing trailing-12m DPS per constituent stock...")
    stock_dps = compute_trailing_12m_dps(div_df, trading_dates)
    logger.info(f"  [{st}]   {len(stock_dps):,} (stock, date) DPS rows")

    logger.info(f"  [{st}] Computing index dividend_yield (cap-weighted constituent "
          f"trailing yields)...")
    dy_df = compute_index_dividend_yield(comp_df, stock_dps, stock_close_df)
    logger.info(f"  [{st}]   {dy_df['dividend_yield'].notna().sum():,} non-null dividend_yield values")

    # ---- Build + insert detail rows --------------------------------------
    logger.info(f"  [{st}] Building detail rows...")
    detail_df = build_detail_rows(close_df, pe, dy_df, st)
    logger.info(f"  [{st}]   {len(detail_df):,} detail rows")

    n_detail = await _write_detail(
        conn, st, detail_df, force=force,
        target_dates_pe=target_dates_pe,
        target_dates_dy=target_dates_dy,
    )

    # ---- Compute + insert monthly stats ----------------------------------
    # Stats always needs full recompute when new month-end dates appear
    # (is_active flag flips). Skip entirely if no missing month-end dates.
    # Single-code mode always recomputes the code's stats rows.
    if force or code is not None or (target_dates_stats is not None and len(target_dates_stats) > 0):
        logger.info(f"  [{st}] Computing monthly 5y rolling stats...")
        pe_df = (
            close_df[["code", "date", "pe"]].copy()
            if "pe" in safe_columns(close_df) else None
        )

        stats_rows = compute_monthly_stats(
            detail_df, pe_df, comp_df, div_df, trading_dates, st
        )
        logger.info(f"  [{st}]   {len(stats_rows):,} monthly stats rows")
        await _write_stats(conn, st, stats_rows, force=force, code=code)
    else:
        logger.info(f"  [{st}] Monthly stats up to date; skipping stats step.")

    # ---- Percentile bands + band-break excursion streaks (internal
    # steps). Bands are incremental (trailing windows are immutable per
    # completed month); streaks shift with new data, so their scope is
    # rebuilt wholesale on every run that processes the sec_type.
    await run_pd_pct_bands(
        conn, detail_df, sec_type=st, force=force, code_filter=code,
    )
    await run_pd_pct_streaks(conn, detail_df, sec_type=st, code_filter=code)

    return n_detail


async def _process_etf(
    conn, pool, *, force: bool, target_dates_pe: set | None,
    target_dates_dy: set | None, target_dates_stats: set | None,
    code: str | None = None,
) -> int:
    """Process sec_type='etf' end-to-end.

    ``code`` (single-code mode, --code): bypasses the active-universe
    pre-filter and recomputes ALL rows for that one code (upsert).
    """
    st = "etf"
    if code is not None:
        code_list = [code]
        logger.info(f"\n  [{st}] SINGLE-CODE mode: processing {code}")
    else:
        logger.info(f"\n  [{st}] Fetching active codes...")
        codes = await fetch_active_codes(conn, st)
        code_list = sorted(codes)
        logger.info(f"  [{st}]   {len(code_list):,} active codes")
        if not code_list:
            logger.info(f"  [{st}]   no active codes; skipping.")
            return 0

    logger.info(f"  [{st}] Fetching close + pe + implied_dividend_per_share...")
    etf_df = await fetch_etf_close_and_dividends(conn, code_list)
    logger.info(f"  [{st}]   {len(etf_df):,} (code, date) rows")

    if code is not None and etf_df.empty:
        logger.info(f"  [{st}]   no source data for {code} in {st}; skipping.")
        return 0

    logger.info(f"  [{st}] Fetching trading dates...")
    trading_dates = await fetch_trading_dates(conn, st)
    logger.info(f"  [{st}]   {len(trading_dates):,} trading dates")

    # Convert ETF adjustment data to dividend events
    div_events = etf_df[etf_df["implied_dividend_per_share"] > 0].copy()
    div_events = div_events.rename(columns={
        "date": "ex_dividend_date",
        "implied_dividend_per_share": "dividend_per_share_pre_tax",
    })[["code", "ex_dividend_date", "dividend_per_share_pre_tax"]]
    logger.info(f"  [{st}]   {len(div_events):,} ETF dividend events")

    close_df = etf_df[["code", "date", "close", "pe"]].copy()

    # Compute pe (ETF PE is pre-computed by builds.etf via harmonic weighting)
    pe_df = close_df[["code", "date", "pe"]].copy()
    pe = clean_pe(close_df)
    logger.info(f"  [{st}]   {pe.notna().sum():,} non-null pe values")

    # Compute dividend_yield
    dy_df = compute_simple_dividend_yield(close_df, div_events)

    # Build + insert detail rows
    detail_df = build_detail_rows(close_df, pe, dy_df, st)
    logger.info(f"  [{st}]   {len(detail_df):,} detail rows")

    n_detail = await _write_detail(
        conn, st, detail_df, force=force,
        target_dates_pe=target_dates_pe,
        target_dates_dy=target_dates_dy,
    )

    # Monthly stats
    if force or code is not None or (target_dates_stats is not None and len(target_dates_stats) > 0):
        logger.info(f"  [{st}] Computing monthly 5y rolling stats...")
        stats_rows = compute_monthly_stats(
            detail_df, pe_df, None, div_events, trading_dates, st
        )
        logger.info(f"  [{st}]   {len(stats_rows):,} monthly stats rows")
        await _write_stats(conn, st, stats_rows, force=force, code=code)
    else:
        logger.info(f"  [{st}] Monthly stats up to date; skipping stats step.")

    # ---- Percentile bands + band-break excursion streaks (see the
    # index processor's comment).
    await run_pd_pct_bands(
        conn, detail_df, sec_type=st, force=force, code_filter=code,
    )
    await run_pd_pct_streaks(conn, detail_df, sec_type=st, code_filter=code)

    return n_detail


async def _process_stock(
    conn, pool, *, force: bool, target_dates_pe: set | None,
    target_dates_dy: set | None, target_dates_stats: set | None,
    code: str | None = None,
) -> int:
    """Process sec_type='stock' end-to-end.

    ``code`` (single-code mode, --code): bypasses the active-universe
    pre-filter and recomputes ALL rows for that one code (upsert).
    """
    st = "stock"
    if code is not None:
        code_list = [code]
        logger.info(f"\n  [{st}] SINGLE-CODE mode: processing {code}")
    else:
        logger.info(f"\n  [{st}] Fetching active codes...")
        codes = await fetch_active_codes(conn, st)
        code_list = sorted(codes)
        logger.info(f"  [{st}]   {len(code_list):,} active codes")
        if not code_list:
            logger.info(f"  [{st}]   no active codes; skipping.")
            return 0

    logger.info(f"  [{st}] Fetching close + pe...")
    close_df = await fetch_stock_close(conn, code_list)
    logger.info(f"  [{st}]   {len(close_df):,} (code, date) rows")

    if code is not None and close_df.empty:
        logger.info(f"  [{st}]   no source data for {code} in {st}; skipping.")
        return 0

    logger.info(f"  [{st}] Fetching dividends (all stock_dividends)...")
    div_df = await fetch_stock_dividends(conn, stock_codes=None)
    logger.info(f"  [{st}]   {len(div_df):,} dividend events total")
    # NOTE: Do NOT strip exchange suffixes for stocks.
    # stats.stock_basic_stats.code and stats.stock_dividends.code are BOTH
    # suffixed (e.g. "600000.SS") and already match each other directly.
    # Stripping would make analysis.pe / analysis.dividends code (stock) BARE,
    # breaking JOINs with stats.stock_basic_stats (chart SQL),
    # stats.stock_identity (codes SQL latest_name), and
    # stats.sec_classification (META_SQL) — all of which are suffixed.

    logger.info(f"  [{st}] Fetching trading dates...")
    trading_dates = await fetch_trading_dates(conn, st)
    logger.info(f"  [{st}]   {len(trading_dates):,} trading dates")

    # Compute pe (stock PE from stock_basic_stats.pe)
    pe_df = close_df[["code", "date", "pe"]].copy()
    pe = clean_pe(close_df)
    logger.info(f"  [{st}]   {pe.notna().sum():,} non-null pe values")

    # Compute dividend_yield
    dy_df = compute_simple_dividend_yield(close_df, div_df)

    # Build + insert detail rows
    detail_df = build_detail_rows(close_df, pe, dy_df, st)
    logger.info(f"  [{st}]   {len(detail_df):,} detail rows")

    n_detail = await _write_detail(
        conn, st, detail_df, force=force,
        target_dates_pe=target_dates_pe,
        target_dates_dy=target_dates_dy,
    )

    # Monthly stats
    if force or code is not None or (target_dates_stats is not None and len(target_dates_stats) > 0):
        logger.info(f"  [{st}] Computing monthly 5y rolling stats...")
        stats_rows = compute_monthly_stats(
            detail_df, pe_df, None, div_df, trading_dates, st
        )
        logger.info(f"  [{st}]   {len(stats_rows):,} monthly stats rows")
        await _write_stats(conn, st, stats_rows, force=force, code=code)
    else:
        logger.info(f"  [{st}] Monthly stats up to date; skipping stats step.")

    # ---- Percentile bands + band-break excursion streaks (see the
    # index processor's comment).
    await run_pd_pct_bands(
        conn, detail_df, sec_type=st, force=force, code_filter=code,
    )
    await run_pd_pct_streaks(conn, detail_df, sec_type=st, code_filter=code)

    return n_detail


_PROCESSORS = {
    "index": _process_index,
    "etf": _process_etf,
    "stock": _process_stock,
}


# ---------------------------------------------------------------------------
#  Shared write helpers (force = DELETE + COPY; incremental = filter + upsert)
# ---------------------------------------------------------------------------

async def _write_metric_table(
    conn, sec_type: str, value_col: str, table: str,
    detail_df: pd.DataFrame, *,
    force: bool, target_dates: set | None,
) -> int:
    """Write one metric's column of the combined detail frame to ``table``
    (rows projected to [sec_type, code, date, value_col]).

    The date + non-null-value filtering happens VECTORIZED on the frame
    BEFORE any sanitize — the former flow sanitized the FULL detail frame
    to dicts first and then python-loop-filtered the dicts, materializing
    ~3 generations of 6.6M row dicts (~5 GB host RSS + tens of seconds of
    client CPU) on daily incremental runs that write only ~1 day of rows.

    - force: DELETE sec_type rows + chunked COPY-insert (the sanitized
      dict list is bounded to one chunk via copy_frame_chunked_async).
    - incremental: filter the frame to target_dates rows, then upsert on
      (sec_type, code, date). Skipped entirely when target_dates is empty.

    The datetime64 ``detail_df`` stays untouched (slices only) so the
    monthly-stats / pct-bands compute can reuse it; ``date_cols`` keeps
    the datetime64 column out of object dtype until sanitize extracts
    python dates host-side.
    """
    if not force and target_dates is not None:
        if len(target_dates) == 0:
            logger.info(f"  [{sec_type}]   {table} up to date; skipping insert.")
            return 0
        # fetch.py incremental-filter convention: isin with a datetime64
        # ndarray (a python-date SET never matches a datetime64 column).
        td64 = pd.to_datetime(sorted(target_dates)).values
        sub = detail_df[detail_df["date"].isin(td64)]
        logger.info(f"  [{sec_type}] Incremental filter: {len(sub):,} of "
              f"{len(detail_df):,} detail rows are in target_dates")
    else:
        sub = detail_df

    sub = sub.loc[
        sub[value_col].notna(), ["sec_type", "code", "date", value_col],
    ]
    if sub.empty:
        logger.info(f"  [{sec_type}]   no rows to write to {table}")
        return 0

    if force:
        logger.info(f"  [{sec_type}] Deleting existing {sec_type} rows from "
              f"{table}...")
        await conn.execute(
            f"DELETE FROM {table} WHERE sec_type = $1", sec_type
        )
        n = await copy_frame_chunked_async(
            conn, table, sub,
            columns=["sec_type", "code", "date", value_col],
            numeric_cols=[value_col], round_to=6,
            date_cols=["date"],
            label=table.split(".")[-1],
        )
        logger.info(f"  [{sec_type}]   inserted {n:,} rows")
        return n

    # Incremental: the frame is already date-filtered — materialize the
    # (small) row dicts and upsert on the PK.
    rows = sanitize_for_db_insert(
        sub, numeric_cols=[value_col], round_to=6, date_cols=["date"],
    )
    logger.info(f"  [{sec_type}] Upserting {len(rows):,} rows into {table}...")
    n_copied, n_upserted = await copy_or_upsert_split_async(
        conn, table, rows,
        key_columns=["sec_type", "code", "date"],
    )
    n = n_copied + n_upserted
    via = "COPY" if n_copied > 0 and n_upserted == 0 else \
          f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
          "upsert"
    logger.info(f"  [{sec_type}]   inserted {n:,} rows via {via}")
    return n


async def _write_detail(
    conn, sec_type: str, detail_df: pd.DataFrame, *,
    force: bool, target_dates_pe: set | None,
    target_dates_dy: set | None,
) -> int:
    """Split the combined detail FRAME per metric and write the TWO tables
    (2026-09 split of analysis.pe_and_dividends):

      analysis.pe         — rows with a defined pe;
      analysis.dividends  — rows with a defined dividend_yield.

    Each side carries its own missing-date set (a date can be missing for
    one metric and present for the other — e.g. a newly-listed payer
    with no PE source yet); both share the force / incremental contract
    of _write_metric_table. Returns the TOTAL rows written.
    """
    n_pe = await _write_metric_table(
        conn, sec_type, "pe", PE_TABLE, detail_df,
        force=force, target_dates=target_dates_pe,
    )
    n_dy = await _write_metric_table(
        conn, sec_type, "dividend_yield", DIVIDENDS_TABLE, detail_df,
        force=force, target_dates=target_dates_dy,
    )
    return n_pe + n_dy


async def _write_stats(
    conn, sec_type: str, stats_rows: list[dict], *, force: bool,
    code: str | None = None,
) -> int:
    """Write monthly stats rows to analysis.pe_and_dividend_stats.

    Always DELETE + COPY-insert. The is_active flag + 5y rolling
    windows require full recompute when a new month-end date appears, so
    there is no per-row upsert path for stats — the caller gates this call
    on whether new month-end dates are actually missing.

    Single-code mode (``code``): deletes only that code's rows first
    (the recomputed stats_rows cover just that code) instead of the
    whole sec_type.
    """
    if not stats_rows:
        logger.info(f"  [{sec_type}]   no stats rows to write")
        return 0

    if code is not None:
        logger.info(f"  [{sec_type}] Deleting existing rows for {code} from "
              f"{STATS_TABLE}...")
        await conn.execute(
            f"DELETE FROM {STATS_TABLE} WHERE sec_type = $1 AND code = $2",
            sec_type, code,
        )
    else:
        logger.info(f"  [{sec_type}] Deleting existing {sec_type} rows from "
              f"{STATS_TABLE}...")
        await conn.execute(
            f"DELETE FROM {STATS_TABLE} WHERE sec_type = $1", sec_type
        )
    logger.info(f"  [{sec_type}] Inserting {len(stats_rows):,} stats rows "
          f"(COPY)...")
    n = await copy_insert_async(conn, STATS_TABLE, stats_rows)
    logger.info(f"  [{sec_type}]   inserted {n:,} stats rows")
    return n


# ---------------------------------------------------------------------------
#  Missing-date detection (incremental mode)
# ---------------------------------------------------------------------------

async def _detect_missing_dates(
    conn, sec_types: list[str], force: bool,
) -> tuple[dict[str, set], dict[str, set]]:
    """Detect missing dates per sec_type for the two metric tables and
    the stats table.

    Returns (target_dates_pe, target_dates_dy, target_dates_stats):
      - target_dates_pe[st]: dates present in the identity table but NOT
        in analysis.pe for that sec_type.
      - target_dates_dy[st]: dates present in the identity table but NOT
        in analysis.dividends for that sec_type.
      - target_dates_stats[st]: MONTH-END trading dates present in the
        identity table but NOT in analysis.pe_and_dividend_stats for that
        sec_type. Used to gate the stats recompute (is_active flips when a
        new month-end appears).

    In force mode all dicts map to None (meaning "all dates").
    """
    if force:
        return (
            {st: None for st in sec_types},
            {st: None for st in sec_types},
            {st: None for st in sec_types},
        )

    target_dates_pe: dict[str, set] = {}
    target_dates_dy: dict[str, set] = {}
    target_dates_stats: dict[str, set] = {}
    for st in sec_types:
        identity_table = SEC_TYPE_IDENTITY_TABLE[st]

        # ---- PE table: missing dates ----
        missing_pe = await find_missing_analysis_dates(
            conn, PE_TABLE, [identity_table], sec_type=st,
        )
        target_dates_pe[st] = set(missing_pe)
        logger.info(f"    -> pe[{st}]: {len(missing_pe)} missing dates")

        # ---- Dividends table: missing dates ----
        missing_dy = await find_missing_analysis_dates(
            conn, DIVIDENDS_TABLE, [identity_table], sec_type=st,
        )

        # ---- Self-heal: exact-zero dividend_yield rows are invalid ----
        # The current formula yields strictly positive values (dps > 0
        # gate) — a stored 0.0 means the row was written by an OLDER
        # compute (which emitted 0 instead of skipping when the
        # trailing-12m dividend sum was empty) and incremental upserts
        # never refreshed it. Those fake 0% stretches make the first real
        # dividend look like an infinite spike. Flag their dates as
        # missing so the upsert overwrites them with the recomputed
        # values.
        zero_dates = await conn.fetch(
            f"SELECT DISTINCT date FROM {DIVIDENDS_TABLE} "
            f"WHERE sec_type = $1 AND dividend_yield = 0",
            st,
        )
        n_zero = len(zero_dates)
        if n_zero:
            missing_dy = set(missing_dy) | {r["date"] for r in zero_dates}
            logger.info(f"    -> dividends[{st}]: {n_zero} dates carry invalid "
                  f"dividend_yield = 0 rows (stale legacy values); "
                  f"re-upserting them")

        target_dates_dy[st] = set(missing_dy)
        logger.info(f"    -> dividends[{st}]: {len(missing_dy)} missing dates")

        # ---- Stats table: missing MONTH-END dates ----
        # The stats table only has month-end rows, so we need to compare
        # month-end trading dates from the identity table against the
        # stats table's dates for this sec_type.
        all_identity_dates = await conn.fetch(
            f'SELECT DISTINCT date FROM {identity_table}'
        )
        identity_dates = sorted(
            {r["date"] for r in all_identity_dates if r["date"] is not None}
        )
        month_ends = set(find_month_end_dates(identity_dates))

        existing_stats_rows = await conn.fetch(
            f'SELECT DISTINCT date FROM {STATS_TABLE} WHERE sec_type = $1',
            st,
        )
        existing_stats_dates = {
            r["date"] for r in existing_stats_rows if r["date"] is not None
        }
        missing_stats = month_ends - existing_stats_dates
        target_dates_stats[st] = missing_stats
        logger.info(f"    -> stats[{st}]: {len(missing_stats)} missing month-end "
              f"dates")

    return target_dates_pe, target_dates_dy, target_dates_stats


class PeDividendsAnalysis(DataAnalysis):
    """``python -m analyze.pe_and_dividends`` — PE + dividend yield.

    The connection AND the fixed-size (4) parallel-write pool are owned
    by the DataAnalysis amain() template (self.conn / self.pool).
    """

    title = "ANALYZE PE & DIVIDENDS (ETF + INDEX + STOCK)"
    sec_types = tuple(SEC_TYPES)
    pool_size = 4
    component = "pe_and_dividends"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        self.add_sec_type_arg(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        if self.args.code and self.args.force:
            logger.error("ERROR: --code and --force are mutually exclusive.")
            sys.exit(2)

    def header_fields(self) -> dict:
        return {
            "pe_table": PE_TABLE,
            "div_table": DIVIDENDS_TABLE,
            "stats_table": STATS_TABLE,
            "sec_types": ", ".join(self.resolve_sec_types()),
            "mode": (
                f"SINGLE-CODE {self.args.code} (full recompute for this security)"
                if self.args.code else
                "FORCE (full recompute per sec_type)" if self.args.force
                else "incremental (missing dates only)"
            ),
        }

    async def run(self) -> None:
        conn = self.conn
        pool = self.pool
        args = self.args
        force = args.force
        sec_types = self.resolve_sec_types()

        # ---- Single-code mode (--code): rebuild ONE security -------------
        # Bypasses the per-sec_type missing-date detection entirely — the
        # UI fires this when a security has NO rows while the rest of the
        # sec_type is up to date (date-level detection would see nothing
        # missing and skip it).
        if args.code:
            total = 0
            for st in sec_types:
                processor = _PROCESSORS[st]
                total += await processor(
                    conn, pool,
                    force=False,
                    target_dates_pe=None,
                    target_dates_dy=None,
                    target_dates_stats=None,
                    code=args.code,
                )

            logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
            await self.upsert_identity(
                ANALYSIS_NAME_PE, "pe", DESCRIPTION_PE,
            )
            await self.upsert_identity(
                ANALYSIS_NAME_DIVIDENDS, "dividends", DESCRIPTION_DIVIDENDS,
            )

            logger.info(f"\n  TOTAL: {total:,} detail rows inserted")
            return

        # ---- Detect missing dates (incremental mode) --------------------
        if not force:
            logger.info("\n  Detecting missing dates per sec_type (incremental mode)...")
        target_dates_pe, target_dates_dy, target_dates_stats = (
            await _detect_missing_dates(conn, list(sec_types), force)
        )

        # Early exit if everything is up to date (incremental mode only).
        if not force:
            total_missing = (
                sum(len(s) for s in target_dates_pe.values())
                + sum(len(s) for s in target_dates_dy.values())
                + sum(len(s) for s in target_dates_stats.values())
            )
            if total_missing == 0:
                logger.info("    -> DB is up to date; nothing to do.")
                return

        total = 0
        for st in sec_types:
            td_pe = target_dates_pe.get(st)
            td_dy = target_dates_dy.get(st)
            td_stats = target_dates_stats.get(st)
            # Skip sec_type entirely if all three targets are up to date.
            if (not force
                    and td_pe is not None and len(td_pe) == 0
                    and td_dy is not None and len(td_dy) == 0
                    and td_stats is not None and len(td_stats) == 0):
                logger.info(f"\n  [{st}] up to date; skipping.")
                continue
            processor = _PROCESSORS[st]
            n = await processor(
                conn, pool,
                force=force,
                target_dates_pe=td_pe,
                target_dates_dy=td_dy,
                target_dates_stats=td_stats,
            )
            total += n

        # Upsert analysis_identity
        logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
        await self.upsert_identity(
            ANALYSIS_NAME_PE, "pe", DESCRIPTION_PE,
        )
        await self.upsert_identity(
            ANALYSIS_NAME_DIVIDENDS, "dividends", DESCRIPTION_DIVIDENDS,
        )

        logger.info(f"\n  TOTAL: {total:,} detail rows inserted")


if __name__ == "__main__":
    PeDividendsAnalysis().execute()
