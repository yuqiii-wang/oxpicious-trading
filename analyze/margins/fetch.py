"""Async DB fetchers for analyze.margins.

All fetchers take an asyncpg ``conn`` and return pandas DataFrames (or
lists of rows) ready for the compute step. The universe filter (only
securities with non-zero rz_balance in the last calendar month) is applied
HERE, at the SQL level, so the downstream pandas code never sees stale
securities.
"""
from __future__ import annotations

import datetime
from typing import Iterable, Set

import pandas as pd

from analyze.margins.config import (
    SRC_TABLE_ETF,
    SRC_TABLE_STOCK,
    UNIVERSE_RECENT_DAYS,
    MIN_ETF_DAILY_MARGIN_YUAN,
)


# ---------------------------------------------------------------------------
#  Universe filter — codes with non-zero rz_balance in the last calendar month
# ---------------------------------------------------------------------------

async def fetch_active_rongzi_codes(
    conn,
    sec_type: str,
    *,
    ref_date: datetime.date | None = None,
) -> Set[str]:
    """Return the set of codes (in the given sec_type's source table) that
    have at least one row with ``rz_balance`` meeting the sec_type's
    minimum threshold in the last ``UNIVERSE_RECENT_DAYS`` calendar days
    on or before ``ref_date``.

    Stale / delisted / suspended securities with no recent rongzi activity
    are excluded from the analysis universe entirely. For ETFs, an
    additional minimum daily rz_balance threshold
    (``MIN_ETF_DAILY_MARGIN_YUAN`` = 1M yuan) is applied — ETFs with
    sub-1M daily margin produce noisy / meaningless slope + zscore
    signals and are filtered out.

    Args:
        conn: asyncpg connection.
        sec_type: 'etf' or 'stock' — picks the source table.
        ref_date: reference "today" for the cutoff (default: real today).
    """
    if ref_date is None:
        ref_date = datetime.date.today()
    cutoff = ref_date - datetime.timedelta(days=UNIVERSE_RECENT_DAYS)
    table = SRC_TABLE_ETF if sec_type == "etf" else SRC_TABLE_STOCK
    # ETFs require a minimum daily rz_balance (1M yuan) — small-margin
    # ETFs produce noisy / meaningless slope + zscore signals.
    min_balance = MIN_ETF_DAILY_MARGIN_YUAN if sec_type == "etf" else 0
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT code
        FROM {table}
        WHERE date >= $1::date
          AND date <= $2::date
          AND rz_balance >= $3::numeric
        """,
        cutoff,
        ref_date,
        min_balance,
    )
    return {r["code"] for r in rows}


# ---------------------------------------------------------------------------
#  Margin history — full per-(code, date) rz_balance + rz_buy
# ---------------------------------------------------------------------------

async def fetch_margin_history(
    conn,
    sec_type: str,
    codes: Iterable[str],
) -> pd.DataFrame:
    """Fetch full per-(code, date) rz_balance and rz_buy for the given
    codes from the appropriate source table.

    Returns a DataFrame with columns: code, date, rz_balance, rz_buy.
    Sorted by (code, date). Only the two rongzi (融资) columns are fetched
    — ronqin (融券 / sec borrow) is intentionally excluded per spec.

    Args:
        conn: asyncpg connection.
        sec_type: 'etf' or 'stock' — picks the source table.
        codes: iterable of code strings to fetch (the universe filter
            should already have been applied).
    """
    code_list = list(codes)
    if not code_list:
        return pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        )

    table = SRC_TABLE_ETF if sec_type == "etf" else SRC_TABLE_STOCK
    rows = await conn.fetch(
        f"""
        SELECT code, date, rz_balance, rz_buy
        FROM {table}
        WHERE code = ANY($1::text[])
        ORDER BY code, date
        """,
        code_list,
    )
    return pd.DataFrame(
        {
            "code": [r["code"] for r in rows],
            "date": [r["date"] for r in rows],
            "rz_balance": [
                float(r["rz_balance"]) if r["rz_balance"] is not None else None
                for r in rows
            ],
            "rz_buy": [
                float(r["rz_buy"]) if r["rz_buy"] is not None else None
                for r in rows
            ],
        }
    )


# ---------------------------------------------------------------------------
#  Index-level margin series — aggregated from the margin_index_series VIEW
# ---------------------------------------------------------------------------

async def fetch_index_margin_series(conn) -> pd.DataFrame:
    """Fetch the per-(index_code, date) aggregated RONGZI margin series from
    the ``analysis.margin_index_series`` VIEW.

    The VIEW aggregates constituent stocks' rz_balance / rz_buy by
    ``parent_index_weight`` (weighted-AVERAGE — see 12_margin.sql header).
    This is the source of the sec_type='index' rows in margin_tech_stats:
    the regime-detection cols (slope / zscore) are computed on this
    AGGREGATED series in Python (aggregate-then-compute — slope is a ratio
    and non-additive, so per-stock slopes cannot be averaged directly).

    Returns a DataFrame with columns: code, date, rz_balance, rz_buy —
    the SAME shape as fetch_margin_history so compute_tech_stats can
    consume it uniformly. ``code`` is the bare 6-digit index code
    (e.g. '000970'); ``rz_balance`` = index_margin_balance (weighted-avg
    yuan); ``rz_buy`` = index_margin_buy (weighted-avg yuan, FLOW).
    Sorted by (code, date).

    No universe filter is applied here — the VIEW already excludes index
    codes with no constituent margin activity, and the per-code
    MIN_HISTORY_DAYS guard in hypes._detect_episodes_for_code filters
    sparse / freshly-listed indices downstream.

    Args:
        conn: asyncpg connection.
    """
    rows = await conn.fetch(
        """
        SELECT
            index_code            AS code,
            date,
            index_margin_balance AS rz_balance,
            index_margin_buy      AS rz_buy
        FROM analysis.margin_index_series
        ORDER BY index_code, date
        """
    )
    if not rows:
        return pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        )
    return pd.DataFrame(
        {
            "code": [r["code"] for r in rows],
            "date": [r["date"] for r in rows],
            "rz_balance": [
                float(r["rz_balance"]) if r["rz_balance"] is not None else None
                for r in rows
            ],
            "rz_buy": [
                float(r["rz_buy"]) if r["rz_buy"] is not None else None
                for r in rows
            ],
        }
    )


# ---------------------------------------------------------------------------
#  Industry mapping — code → (industry_id, industry_label, parent_weight)
# ---------------------------------------------------------------------------

async def fetch_industry_mapping(
    conn,
    sec_type: str,
) -> pd.DataFrame:
    """Fetch the (code → industry_id) mapping for the given sec_type.

    Mapping convention (ALL classification types — industries,
    broad-markets, and strategies are included so the margin_industry_stats
    table covers the full universe):
      Stock → industry_id via sec_classification WHERE type='stock'
              AND parent_index_is_primary=TRUE. Stocks always have an
              industry parent (build rule excludes BROAD indices from
              stock rows), so is_industry_not_strategy=TRUE for all
              stock rows — the filter is redundant but kept for clarity.
      ETF   → industry_id via TWO-HOP JOIN (mirrors stats.etf_trading_amt):
                etf.parent_index_code → index.industry_id
              ALL index types are included (is_industry_not_strategy
              TRUE or FALSE) so ETFs tracking BROAD-market indices
              (000300, 000905, …) and strategy indices also contribute
              to margin_industry_stats. This was previously restricted
              to is_industry_not_strategy=TRUE only; the filter was
              removed to enable ALL classification types per spec.

    Returns a DataFrame with columns: code, industry_id, industry_label,
    parent_index_weight. One row per (code, industry_id) — a security
    appears at most once.

    Args:
        conn: asyncpg connection.
        sec_type: 'etf' or 'stock'.
    """
    if sec_type == "stock":
        # Stock: primary row already carries the industry directly
        # (build rule guarantees parent is an industry index, not BROAD).
        # is_industry_not_strategy=TRUE for all stock rows — redundant
        # but kept for documentation.
        rows = await conn.fetch(
            """
            SELECT
                code,
                industry_id,
                industry_label,
                parent_index_weight
            FROM stats.sec_classification
            WHERE type = 'stock'
              AND parent_index_is_primary = TRUE
              AND industry_id IS NOT NULL
              AND industry_id <> ''
            """
        )
    else:
        # ETF: two-hop via parent_index_code → index.industry_id.
        # ALL index types are included (no is_industry_not_strategy
        # filter) so broad-market + strategy ETFs also contribute.
        rows = await conn.fetch(
            """
            SELECT
                e.code,
                idx.industry_id,
                idx.industry_label,
                e.parent_index_weight
            FROM stats.sec_classification e
            JOIN LATERAL (
                SELECT DISTINCT industry_id, industry_label
                FROM stats.sec_classification
                WHERE type = 'index'
                  AND is_active = TRUE
                  AND industry_id IS NOT NULL
                  AND industry_id <> ''
                  AND code = e.parent_index_code
            ) idx ON TRUE
            WHERE e.type = 'etf'
              AND e.parent_index_is_primary = TRUE
              AND e.parent_index_code <> ''
            """
        )

    return pd.DataFrame(
        {
            "code": [r["code"] for r in rows],
            "industry_id": [r["industry_id"] for r in rows],
            "industry_label": [r["industry_label"] for r in rows],
            "parent_index_weight": [
                float(r["parent_index_weight"])
                if r["parent_index_weight"] is not None
                else None
                for r in rows
            ],
        }
    )
