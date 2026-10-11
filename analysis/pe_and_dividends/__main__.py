"""Entry point for analysis.pe_and_dividends.

Run via ``python -m analysis.pe_and_dividends``.

Pipeline (default: incremental — skip dates already in the DB;
``--force``: truncate-then-recompute on every run).

Metric STAGES (2026-10-03 run split, mirroring the 2026-09 table
split): ``--metrics pe`` / ``--metrics dividends`` / ``--metrics tech``
/ ``--metrics deliveries`` (comma-separated; default: all four in one
process). The stages are independent end to end — 'pe' writes
analysis.pe and runs the pe_pct bands + pe_pct_streaks internal steps
WITHOUT any dividend-side fetch (sec_composition, stock_dividends,
constituent closes); 'dividends' computes the yields WITHOUT the pe
clean or the bands/streaks steps; 'tech' writes the dividend price
estimation curve (analysis.dividend_est — ETF stage only for now;
index/stock skip with a log line) from the ETF close +
implied_dividend fetch alone; 'deliveries' writes the rolling 5y
(1y-stride) dividend delivery audit analysis.dividend_deliveries (etf +
stock stages; index skips) from the close/eps + delivery-event fetches
— a tiny wholesale-rebuild stage with NO missing-date gating (the
pe_pct_streaks precedent).
Missing-date detection, the up-to-date skips, ``--force`` scoping and
the analysis_identity upserts are all per selected stage.

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
     (trailing-12m D/P) on FULL history (trailing-12m DPS windows need
     it).
  4. Write daily rows to analysis.pe + analysis.dividends (2026-09 split):
     - ``--force``: DELETE sec_type rows + COPY-insert.
     - default: upsert ONLY rows whose date is in the missing-dates set.
  5. Compute monthly trailing percentile BANDS of the pe series
     (analysis.pe_pct — internal step pe/pct_bands.py): ``--force`` /
     ``--code`` DELETE the scope + rebuild; incremental computes only
     missing (code, month) triples (trailing windows make completed
     months immutable).
  6. Compute band-BREAK excursion streaks of pe against those bands
     (analysis.pe_pct_streaks — internal step pe/pct_streaks.py):
     episodes shift with new data, so the scope is rebuilt WHOLESALE per
     sec_type (per code in --code mode) on every run that processes it.
  7. Refresh the per-code summary analysis.pe_codes (internal step
     _common/codes_summary.py — the /api/analysis/pe/codes endpoint's
     precomputed source) for every sec_type the run wrote.
  8. Upsert analysis_identity.

(The former annual 10y rolling stats stage — analysis.pe_stats /
analysis.dividends_stats — was removed 2026-09-28 with its only
consumer, the UI's Dividend Stats card.)

Incremental mode rationale
  The pe and dividend_yield for past dates don't change retroactively
  (PE and dividends are historical facts), so existing rows are valid.
  New dates get appended via upsert.
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
    copy_frame_chunked_async,
    chunked_purge_async,
)

import pandas as pd

from analysis._common.sanitize import sanitize_for_db_insert
from _common.df_utils import to_dt64
from _common.df_utils.sanitize import host_unique, safe_columns
from analysis.pe_and_dividends._common import build_detail_rows
from analysis.pe_and_dividends._common.codes_summary import (
    refresh_pe_codes_summary,
)
from analysis.pe_and_dividends._common.config import (
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)
from analysis.pe_and_dividends._common.fetch import (
    fetch_active_codes,
    fetch_index_pe_and_close,
    fetch_latest_index_composition,
    fetch_stock_dividends,
    fetch_constituent_closes,
    fetch_etf_close_and_dividends,
    fetch_stock_close,
    fetch_trading_dates,
)
from analysis.pe_and_dividends.dividends.compute import (
    compute_trailing_12m_dps,
    compute_index_dividend_yield,
    compute_simple_dividend_yield,
)
from analysis.pe_and_dividends.dividends.config import (
    ANALYSIS_NAME_DELIVERIES,
    ANALYSIS_NAME_DIVIDENDS,
    ANALYSIS_NAME_TECH,
    DELIVERIES_TABLE,
    DESCRIPTION_DELIVERIES,
    DESCRIPTION_DIVIDENDS,
    DESCRIPTION_TECH,
    DIVIDENDS_TABLE,
    TECH_TABLE,
)
from analysis.pe_and_dividends.dividends.deliveries import (
    run_dividend_deliveries,
)
from analysis.pe_and_dividends.dividends.tech_stats import run_dividend_tech
from analysis.pe_and_dividends.pe.compute import clean_pe
from analysis.pe_and_dividends.pe.config import (
    ANALYSIS_NAME_PE,
    DESCRIPTION_PE,
    PE_TABLE,
)
from analysis.pe_and_dividends.pe.pct_bands import run_pe_pct_bands
from analysis.pe_and_dividends.pe.pct_streaks import run_pe_pct_streaks

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis
from _common.labels import SEC_TYPE_CODE

logger = setup_logging("pe_and_dividends")

# The per-metric stage keys --metrics accepts (the analysis_forecasts
# --forecast-metrics pattern): the split tables run as independent
# stages.
STAGE_NAMES = ("pe", "dividends", "tech", "deliveries")


def _normalize_stock_codes(df, col: str) -> None:
    """Normalize a stock-code column in-place by stripping exchange suffixes.

    Vectorized str ops (two kernels total) — replaces the former per-row
    .apply, which paid one proxy Series.__getitem__ fallback per cell."""
    if col in safe_columns(df):
        df[col] = df[col].str.upper().str.replace(
            r"\.(SS|SZ|SH|BJ|HK)$", "", regex=True
        )


async def _process_index(
    conn, pool, *, force: bool, metrics: frozenset[str],
    target_dates_pe: set | None,
    target_dates_dy: set | None,
    target_dates_tech: set | None,
    code: str | None = None,
) -> int:
    """Process sec_type='index' for the --metrics-selected stages.

    ``code`` (single-code mode, --code): bypasses the active-universe
    pre-filter and recomputes ALL rows for that one code (upsert).
    """
    st = "index"
    if "tech" in metrics:
        logger.info(f"\n  [{st}] tech stage: etf-only for now; skipping.")
    if "deliveries" in metrics:
        logger.info(f"\n  [{st}] deliveries stage: etf/stock only (an index "
              f"has no per-share deliveries); skipping.")
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

    # ---- Dividend-side fetches + compute (dividends stage only) ----------
    # The index yield needs the LATEST composition, ALL stock dividend
    # events and every constituent's close history — the pe stage skips
    # this whole block.
    if "dividends" in metrics:
        logger.info(f"  [{st}] Fetching latest composition from sec_composition...")
        comp_df = await fetch_latest_index_composition(conn, code_list)
        logger.info(f"  [{st}]   {len(comp_df):,} (index_code, stock_code) composition rows")

        # Get unique constituent stock codes (original format, before normalization)
        if not comp_df.empty:
            constituent_codes = host_unique(comp_df["stock_code"])
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

        logger.info(f"  [{st}] Computing trailing-12m DPS per constituent stock...")
        stock_dps = compute_trailing_12m_dps(div_df, trading_dates)
        logger.info(f"  [{st}]   {len(stock_dps):,} (stock, date) DPS rows")

        logger.info(f"  [{st}] Computing index dividend_yield (cap-weighted constituent "
              f"trailing yields)...")
        dy_df = compute_index_dividend_yield(comp_df, stock_dps, stock_close_df)
        logger.info(f"  [{st}]   {dy_df['dividend_yield'].notna().sum():,} non-null dividend_yield values")
    else:
        logger.info(f"  [{st}] dividends stage not selected (--metrics); "
              f"skipping dividend-side fetches + compute")
        dy_df = pd.DataFrame()

    # ---- Compute pe (pe stage only) ---------------------------------------
    if "pe" in metrics:
        logger.info(f"  [{st}] Cleaning pe (masking invalid <= 0 / NULL values)...")
        pe = clean_pe(close_df)
        logger.info(f"  [{st}]   {pe.notna().sum():,} non-null pe values")
    else:
        pe = None

    # ---- Build + insert detail rows --------------------------------------
    logger.info(f"  [{st}] Building detail rows...")
    detail_df = build_detail_rows(close_df, pe, dy_df, st)
    logger.info(f"  [{st}]   {len(detail_df):,} detail rows")

    n_detail = await _write_detail(
        conn, st, detail_df, force=force, metrics=metrics,
        target_dates_pe=target_dates_pe,
        target_dates_dy=target_dates_dy,
    )

    # ---- Percentile bands + band-break excursion streaks (internal
    # PE-only steps). Bands are incremental (trailing windows are immutable per
    # completed month); streaks shift with new data, so their scope is
    # rebuilt wholesale on every run that processes the pe stage.
    if "pe" in metrics:
        await run_pe_pct_bands(
            conn, detail_df, sec_type=st, force=force, code_filter=code,
        )
        await run_pe_pct_streaks(conn, detail_df, sec_type=st, code_filter=code)

    return n_detail


async def _process_etf(
    conn, pool, *, force: bool, metrics: frozenset[str],
    target_dates_pe: set | None,
    target_dates_dy: set | None,
    target_dates_tech: set | None,
    code: str | None = None,
) -> int:
    """Process sec_type='etf' for the --metrics-selected stages.

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

    close_df = etf_df[["code", "date", "close", "pe"]].copy()

    # ---- Dividend price estimation curve (tech stage only) ----------------
    # Needs only the already-fetched close + implied_dividend columns.
    if "tech" in metrics:
        n_tech = await run_dividend_tech(
            conn, st, etf_df, force=force, code=code,
            target_dates=target_dates_tech,
        )
    else:
        logger.info(f"  [{st}] tech stage not selected (--metrics); "
              f"skipping estimation curve")
        n_tech = 0

    # ---- Rolling 5y delivery audit (deliveries stage only) ----------------
    if "deliveries" in metrics:
        div_events = etf_df[etf_df["implied_dividend_per_share"] > 0].copy()
        div_events = div_events.rename(columns={
            "date": "ex_dividend_date",
            "implied_dividend_per_share": "dividend_per_share_pre_tax",
        })[["code", "ex_dividend_date", "dividend_per_share_pre_tax"]]
        n_dl = await run_dividend_deliveries(
            conn, st,
            etf_df[["code", "date", "close", "eps"]],
            div_events,
            force=force, code=code,
        )
    else:
        logger.info(f"  [{st}] deliveries stage not selected (--metrics); "
              f"skipping delivery audit")
        n_dl = 0

    # ---- Remaining stages need the detail frame — a tech/deliveries-only
    # run stops here.
    if "pe" not in metrics and "dividends" not in metrics:
        return n_tech + n_dl

    # ---- Dividend-side fetches + compute (dividends stage only) ----------
    if "dividends" in metrics:
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

        # Compute dividend_yield
        dy_df = compute_simple_dividend_yield(close_df, div_events)
    else:
        logger.info(f"  [{st}] dividends stage not selected (--metrics); "
              f"skipping dividend-side compute")
        dy_df = pd.DataFrame()

    # ---- Compute pe (pe stage only) ---------------------------------------
    # (ETF PE is pre-computed by builds.etf via harmonic weighting)
    if "pe" in metrics:
        pe_df = close_df[["code", "date", "pe"]].copy()
        pe = clean_pe(close_df)
        logger.info(f"  [{st}]   {pe.notna().sum():,} non-null pe values")
    else:
        pe = None

    # Build + insert detail rows
    detail_df = build_detail_rows(close_df, pe, dy_df, st)
    logger.info(f"  [{st}]   {len(detail_df):,} detail rows")

    n_detail = await _write_detail(
        conn, st, detail_df, force=force, metrics=metrics,
        target_dates_pe=target_dates_pe,
        target_dates_dy=target_dates_dy,
    )

    # ---- Percentile bands + band-break excursion streaks (PE-only
    # internal steps — see the index processor's comment).
    if "pe" in metrics:
        await run_pe_pct_bands(
            conn, detail_df, sec_type=st, force=force, code_filter=code,
        )
        await run_pe_pct_streaks(conn, detail_df, sec_type=st, code_filter=code)

    return n_detail + n_tech + n_dl


async def _process_stock(
    conn, pool, *, force: bool, metrics: frozenset[str],
    target_dates_pe: set | None,
    target_dates_dy: set | None,
    target_dates_tech: set | None,
    code: str | None = None,
) -> int:
    """Process sec_type='stock' for the --metrics-selected stages.

    ``code`` (single-code mode, --code): bypasses the active-universe
    pre-filter and recomputes ALL rows for that one code (upsert).
    """
    st = "stock"
    if "tech" in metrics:
        logger.info(f"\n  [{st}] tech stage: etf-only for now; skipping.")
    if "deliveries" in metrics:
        logger.info(f"\n  [{st}] deliveries stage: processing (rolling 5y "
              f"delivery audit).")
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

    # ---- Dividend-side fetches + compute (dividends / deliveries stages) --
    div_df = None
    if "dividends" in metrics or "deliveries" in metrics:
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

    # ---- Rolling 5y delivery audit (deliveries stage only) ----------------
    if "deliveries" in metrics:
        n_dl = await run_dividend_deliveries(
            conn, st, close_df, div_df, force=force, code=code,
        )
    else:
        logger.info(f"  [{st}] deliveries stage not selected (--metrics); "
              f"skipping delivery audit")
        n_dl = 0

    # ---- Dividends stage: trailing-12m yield ------------------------------
    if "dividends" in metrics:
        logger.info(f"  [{st}] Fetching trading dates...")
        trading_dates = await fetch_trading_dates(conn, st)
        logger.info(f"  [{st}]   {len(trading_dates):,} trading dates")

        # Compute dividend_yield
        dy_df = compute_simple_dividend_yield(close_df, div_df)
    else:
        logger.info(f"  [{st}] dividends stage not selected (--metrics); "
              f"skipping dividend-side compute")
        dy_df = pd.DataFrame()

    # ---- A deliveries-only run stops here (no detail frame needed) --------
    if "pe" not in metrics and "dividends" not in metrics:
        return n_dl

    # ---- Compute pe (pe stage only) ---------------------------------------
    # (stock PE from stock_basic_stats.pe)
    if "pe" in metrics:
        pe_df = close_df[["code", "date", "pe"]].copy()
        pe = clean_pe(close_df)
        logger.info(f"  [{st}]   {pe.notna().sum():,} non-null pe values")
    else:
        pe = None

    # Build + insert detail rows
    detail_df = build_detail_rows(close_df, pe, dy_df, st)
    logger.info(f"  [{st}]   {len(detail_df):,} detail rows")

    n_detail = await _write_detail(
        conn, st, detail_df, force=force, metrics=metrics,
        target_dates_pe=target_dates_pe,
        target_dates_dy=target_dates_dy,
    )

    # ---- Percentile bands + band-break excursion streaks (PE-only
    # internal steps — see the index processor's comment).
    if "pe" in metrics:
        await run_pe_pct_bands(
            conn, detail_df, sec_type=st, force=force, code_filter=code,
        )
        await run_pe_pct_streaks(conn, detail_df, sec_type=st, code_filter=code)

    return n_detail + n_dl


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
        td64 = to_dt64(sorted(target_dates))
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
        await chunked_purge_async(
            conn, table, where_sql="sec_type = $1", params=(SEC_TYPE_CODE[sec_type],)
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
    force: bool, metrics: frozenset[str],
    target_dates_pe: set | None,
    target_dates_dy: set | None,
) -> int:
    """Split the combined detail FRAME per metric and write the TWO tables
    (2026-09 split of analysis.pe_and_dividends; 2026-10-03 run split —
    each side only when its --metrics stage is selected):

      analysis.pe         — rows with a defined pe;
      analysis.dividends  — rows with a defined dividend_yield.

    Each side carries its own missing-date set (a date can be missing for
    one metric and present for the other — e.g. a newly-listed payer
    with no PE source yet); both share the force / incremental contract
    of _write_metric_table. Returns the TOTAL rows written.
    """
    n = 0
    if "pe" in metrics:
        n += await _write_metric_table(
            conn, sec_type, "pe", PE_TABLE, detail_df,
            force=force, target_dates=target_dates_pe,
        )
    if "dividends" in metrics:
        n += await _write_metric_table(
            conn, sec_type, "dividend_yield", DIVIDENDS_TABLE, detail_df,
            force=force, target_dates=target_dates_dy,
        )
    return n


# ---------------------------------------------------------------------------
#  Missing-date detection (incremental mode)
# ---------------------------------------------------------------------------

async def _detect_missing_dates(
    conn, sec_types: list[str], force: bool,
    metrics: frozenset[str],
) -> tuple[dict[str, set], dict[str, set], dict[str, set]]:
    """Detect missing dates per sec_type for the SELECTED metric tables
    (--metrics; unselected stages resolve to EMPTY sets — their tables are
    not this run's target and never block the up-to-date skips).

    Returns (target_dates_pe, target_dates_dy, target_dates_tech):
      - target_dates_pe[st]: dates present in the identity table but NOT
        in analysis.pe for that sec_type.
      - target_dates_dy[st]: dates present in the identity table but NOT
        in analysis.dividends for that sec_type.
      - target_dates_tech[st]: same vs analysis.dividend_est —
        ETF ONLY for now (the curve's only scope); other sec_types
        resolve to empty sets.

    The 'deliveries' stage is NOT date-gated (its annual windows are a
    tiny wholesale rebuild — see run()); it participates in this
    function only through the doc note that unselected stages resolve to
    empty sets.

    In force mode the dicts map to None (meaning "all dates").
    """
    if force:
        return (
            {st: None for st in sec_types},
            {st: None for st in sec_types},
            {st: None for st in sec_types},
        )

    target_dates_pe: dict[str, set] = {}
    target_dates_dy: dict[str, set] = {}
    target_dates_tech: dict[str, set] = {}
    for st in sec_types:
        identity_table = SEC_TYPE_IDENTITY_TABLE[st]

        # ---- PE table: missing dates ----
        if "pe" in metrics:
            missing_pe = await find_missing_analysis_dates(
                conn, PE_TABLE, [identity_table], sec_type=SEC_TYPE_CODE[st],
            )
            target_dates_pe[st] = set(missing_pe)
            logger.info(f"    -> pe[{st}]: {len(missing_pe)} missing dates")
        else:
            target_dates_pe[st] = set()

        # ---- Dividends table: missing dates ----
        if "dividends" in metrics:
            missing_dy = await find_missing_analysis_dates(
                conn, DIVIDENDS_TABLE, [identity_table], sec_type=SEC_TYPE_CODE[st],
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
                SEC_TYPE_CODE[st],
            )
            n_zero = len(zero_dates)
            if n_zero:
                missing_dy = set(missing_dy) | {r["date"] for r in zero_dates}
                logger.info(f"    -> dividends[{st}]: {n_zero} dates carry invalid "
                      f"dividend_yield = 0 rows (stale legacy values); "
                      f"re-upserting them")

            target_dates_dy[st] = set(missing_dy)
            logger.info(f"    -> dividends[{st}]: {len(missing_dy)} missing dates")
        else:
            target_dates_dy[st] = set()

        # ---- Tech table: missing dates (ETF only for now) ----
        if "tech" in metrics:
            if st == "etf":
                missing_tech = await find_missing_analysis_dates(
                    conn, TECH_TABLE, [identity_table], sec_type=SEC_TYPE_CODE[st],
                )
                target_dates_tech[st] = set(missing_tech)
                logger.info(f"    -> tech[{st}]: {len(missing_tech)} missing dates")
            else:
                # Non-etf sec_types have no tech rows by design — an empty
                # set (NOT the missing set) so they never block the
                # up-to-date early exit.
                target_dates_tech[st] = set()
                logger.info(f"    -> tech[{st}]: etf-only stage; no target dates")
        else:
            target_dates_tech[st] = set()

    return target_dates_pe, target_dates_dy, target_dates_tech


class PeDividendsAnalysis(DataAnalysis):
    """``python -m analysis.pe_and_dividends`` — PE + dividend yield +
    dividend price estimation curve + rolling delivery audit, as
    independently selectable metric stages (--metrics pe / dividends /
    tech / deliveries; default: all in one process).

    The connection AND the fixed-size (4) parallel-write pool are owned
    by the DataAnalysis amain() template (self.conn / self.pool).
    """

    title = "ANALYZE PE & DIVIDENDS (ETF + INDEX + STOCK)"
    sec_types = tuple(SEC_TYPES)
    pool_size = 4
    component = "pe_and_dividends"
    # The selected metric stages (frozenset of STAGE_NAMES; set in
    # apply_args from --metrics — default: both).
    metrics: frozenset[str] = frozenset(STAGE_NAMES)

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        self.add_sec_type_arg(parser)
        self.add_code_arg(parser)
        parser.add_argument(
            "--metrics", type=str, default=None, metavar="STAGES",
            help="Comma-separated metric stages to run (choices: "
                 f"{', '.join(STAGE_NAMES)}; default: all — one process "
                 "computes every table). The stages are independent end "
                 "to end: 'pe' writes analysis.pe + the pe_pct bands / "
                 "pe_pct_streaks internal steps without any dividend-side "
                 "fetch; 'dividends' computes the yields without the pe "
                 "clean or those steps; 'tech' writes the dividend price "
                 "estimation curve analysis.dividend_est (ETF stage "
                 "only for now); 'deliveries' writes the rolling 5y "
                 "dividend delivery audit analysis.dividend_deliveries "
                 "(etf + stock; wholesale rebuild). The daily task runs "
                 "the stages as separate invocations.",
        )

    def apply_args(self) -> None:
        if self.args.code and self.args.force:
            logger.error("ERROR: --code and --force are mutually exclusive.")
            sys.exit(2)
        if self.args.metrics:
            requested = {
                s.strip() for s in self.args.metrics.split(",") if s.strip()
            }
            unknown = requested - set(STAGE_NAMES)
            if unknown:
                self.parser.error(
                    f"--metrics: unknown stage(s) {sorted(unknown)} "
                    f"(choices: {', '.join(STAGE_NAMES)})"
                )
            self.metrics = frozenset(requested)
        else:
            self.metrics = frozenset(STAGE_NAMES)

    def header_fields(self) -> dict:
        return {
            "pe_table": PE_TABLE,
            "div_table": DIVIDENDS_TABLE,
            "tech_table": TECH_TABLE,
            "deliveries_table": DELIVERIES_TABLE,
            "sec_types": ", ".join(self.resolve_sec_types()),
            "metrics": " + ".join(
                m for m in STAGE_NAMES if m in self.metrics
            ),
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
        metrics = self.metrics
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
                    metrics=metrics,
                    target_dates_pe=None,
                    target_dates_dy=None,
                    target_dates_tech=None,
                    code=args.code,
                )

            logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
            if "pe" in metrics:
                await self.upsert_identity(
                    ANALYSIS_NAME_PE, "pe", DESCRIPTION_PE,
                )
            if "dividends" in metrics:
                await self.upsert_identity(
                    ANALYSIS_NAME_DIVIDENDS, "dividends", DESCRIPTION_DIVIDENDS,
                )
            if "tech" in metrics:
                await self.upsert_identity(
                    ANALYSIS_NAME_TECH, "dividend_est", DESCRIPTION_TECH,
                )
            if "deliveries" in metrics:
                await self.upsert_identity(
                    ANALYSIS_NAME_DELIVERIES, "dividend_deliveries",
                    DESCRIPTION_DELIVERIES,
                )

            # Refresh the per-code summary the /codes endpoint reads
            # (scoped to the one rebuilt code; it reads BOTH detail tables
            # from the DB, so a one-stage run still refreshes the whole
            # summary row correctly).
            await refresh_pe_codes_summary(conn, sec_types, code=args.code)

            logger.info(f"\n  TOTAL: {total:,} detail rows inserted")
            return

        # ---- Detect missing dates (incremental mode) --------------------
        if not force:
            logger.info("\n  Detecting missing dates per sec_type (incremental mode)...")
        target_dates_pe, target_dates_dy, target_dates_tech = (
            await _detect_missing_dates(conn, list(sec_types), force, metrics)
        )

        # Early exit if everything is up to date (incremental mode only).
        # The 'deliveries' stage is NOT date-gated (tiny wholesale
        # rebuild) — it must keep the run alive when selected.
        if not force:
            total_missing = (
                sum(len(s) for s in target_dates_pe.values())
                + sum(len(s) for s in target_dates_dy.values())
                + sum(len(s) for s in target_dates_tech.values())
            )
            if total_missing == 0 and "deliveries" not in metrics:
                logger.info("    -> DB is up to date; nothing to do.")
                return

        total = 0
        processed_sec_types: list[str] = []
        for st in sec_types:
            td_pe = target_dates_pe.get(st)
            td_dy = target_dates_dy.get(st)
            td_tech = target_dates_tech.get(st)
            # Skip sec_type entirely if every SELECTED stage's target set
            # is empty (unselected stages resolve to empty sets above).
            # 'deliveries' (when selected) always counts as pending — its
            # wholesale rebuild has no missing-date set to consult.
            pending = 0
            if "pe" in metrics and td_pe is not None:
                pending += len(td_pe)
            if "dividends" in metrics and td_dy is not None:
                pending += len(td_dy)
            if "tech" in metrics and td_tech is not None:
                pending += len(td_tech)
            if "deliveries" in metrics:
                pending += 1
            if not force and pending == 0:
                logger.info(f"\n  [{st}] up to date; skipping.")
                continue
            processor = _PROCESSORS[st]
            n = await processor(
                conn, pool,
                force=force,
                metrics=metrics,
                target_dates_pe=td_pe,
                target_dates_dy=td_dy,
                target_dates_tech=td_tech,
            )
            total += n
            processed_sec_types.append(st)

        # Upsert analysis_identity (selected stages only)
        logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
        if "pe" in metrics:
            await self.upsert_identity(
                ANALYSIS_NAME_PE, "pe", DESCRIPTION_PE,
            )
        if "dividends" in metrics:
            await self.upsert_identity(
                ANALYSIS_NAME_DIVIDENDS, "dividends", DESCRIPTION_DIVIDENDS,
            )
        if "tech" in metrics:
            await self.upsert_identity(
                ANALYSIS_NAME_TECH, "dividend_est", DESCRIPTION_TECH,
            )
        if "deliveries" in metrics:
            await self.upsert_identity(
                ANALYSIS_NAME_DELIVERIES, "dividend_deliveries",
                DESCRIPTION_DELIVERIES,
            )

        # Refresh the per-code summary the /codes endpoint reads — only for
        # the sec_types this run actually wrote (a skipped sec_type's rows
        # are unchanged, so its summary rows stay valid). The summary reads
        # BOTH detail tables from the DB, so a one-stage run still
        # recomputes the other metric's latest snapshot from its existing
        # rows.
        if processed_sec_types:
            await refresh_pe_codes_summary(conn, processed_sec_types)

        logger.info(f"\n  TOTAL: {total:,} detail rows inserted")


if __name__ == "__main__":
    PeDividendsAnalysis().execute()
