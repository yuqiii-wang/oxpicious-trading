"""Row contracts for the market-participation pipeline (class-first per
oxpicious-dev-conventions — pydantic carriers crossing module
boundaries; they never ride inside frame compute, materializing only at
the write boundary)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class MarketParticipationRow(BaseModel):
    """One analysis.market_participation row (grain: date). Mirrors
    database/sql/analysis/31_market_participation.sql; the pipeline's
    OUT_COLUMNS / numeric-column lists derive from the field order so
    the frame->row contract stays single-sourced."""

    date: object  # datetime.date at the write boundary

    # Market turnover (stats.exchange_trading_amt proxies)
    total_trading_amount: float
    ss_trading_amount: float
    sz_trading_amount: float
    total_amt_ma5: Optional[float] = None
    total_amt_ma20: Optional[float] = None
    total_amt_ma60: Optional[float] = None
    total_amt_vs_ma5: Optional[float] = None
    total_amt_vs_ma20: Optional[float] = None
    total_amt_vs_ma60: Optional[float] = None
    total_amt_pct_rank_252: Optional[float] = None

    # SSE share of market turnover
    ss_share: float
    ss_share_ma5: Optional[float] = None
    ss_share_ma20: Optional[float] = None
    ss_share_ma60: Optional[float] = None
    ss_share_vs_ma20_pp: Optional[float] = None
    ss_share_slope20_pp: Optional[float] = None

    # Advance/decline breadth (whole stock universe)
    adv_count: Optional[int] = None
    dec_count: Optional[int] = None
    flat_count: Optional[int] = None
    ad_ratio: Optional[float] = None
    median_pct_change: Optional[float] = None
    mean_pct_change: Optional[float] = None
    ad_ratio_ma5: Optional[float] = None
    ad_ratio_ma20: Optional[float] = None
    ad_ratio_pct_rank_252: Optional[float] = None


# The DB column order (the frame's write boundary contract).
OUT_COLUMNS: list[str] = list(MarketParticipationRow.model_fields.keys())

# float64 frame columns (everything except date + the three int counts).
OUT_FLOAT_COLUMNS: list[str] = [
    name for name in OUT_COLUMNS
    if name not in ("date", "adv_count", "dec_count", "flat_count")
]
