"""Row contracts for the volatility pipeline (class-first per
oxpicious-dev-conventions — pydantic carriers crossing module
boundaries; they never ride inside frame compute, materializing only
at the write boundary)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class VolatilityRow(BaseModel):
    """One analysis.volatility row (grain: sec_type + code + date).
    Mirrors database/sql/analysis/volatility.sql; the pipeline's OUT_*
    column lists derive from the field order so the frame->row contract
    stays single-sourced. All *vol* fields are percent annualized
    (daily × √252 × 100); atr_14 is a price level."""

    sec_type: int  # static.label_dim 'sec_type' SMALLINT code (1 = index)
    code: str
    date: object   # datetime.date at the write boundary

    # Close-to-close historical vol (std of daily log returns, ddof=1)
    hv_5: Optional[float] = None
    hv_10: Optional[float] = None
    hv_20: Optional[float] = None
    hv_60: Optional[float] = None
    hv_120: Optional[float] = None
    hv_252: Optional[float] = None

    # RiskMetrics EWMA vol (λ=0.94, contemporaneous)
    ewma_vol: Optional[float] = None

    # Daily-OHLC range estimators (20-row mean of daily variance terms)
    parkinson_vol_20: Optional[float] = None
    garman_klass_vol_20: Optional[float] = None
    rogers_satchell_vol_20: Optional[float] = None
    yang_zhang_vol_20: Optional[float] = None

    # Overnight vs intraday decomposition (20-row)
    overnight_vol_20: Optional[float] = None
    intraday_vol_20: Optional[float] = None

    # ATR family (Wilder 14)
    atr_14: Optional[float] = None
    natr_14: Optional[float] = None

    # Band width / vol structure gauges
    boll_bw_20: Optional[float] = None
    vol_of_vol_20: Optional[float] = None
    vol_ratio_20_252: Optional[float] = None

    # Regime gauges: trailing-252 percentile (0..1) + z-score
    hv_20_pct_rank_252: Optional[float] = None
    ewma_vol_pct_rank_252: Optional[float] = None
    yang_zhang_vol_20_pct_rank_252: Optional[float] = None
    natr_14_pct_rank_252: Optional[float] = None
    hv_20_z_252: Optional[float] = None
    ewma_vol_z_252: Optional[float] = None

    # 5-minute realized vol leg (NULL where intraday bars are absent)
    rv_5min: Optional[float] = None
    jump_ratio_5min: Optional[float] = None
    rv_bar_count: Optional[int] = None


# The DB column order (the frame's write boundary contract).
OUT_COLUMNS: list[str] = list(VolatilityRow.model_fields.keys())

# float64 frame columns (everything except the PK trio + the int count).
OUT_FLOAT_COLUMNS: list[str] = [
    name for name in OUT_COLUMNS
    if name not in ("sec_type", "code", "date", "rv_bar_count")
]

# Columns handed to sanitize_for_db_insert's numeric branch (floats
# round/NaN-swept; the nullable-int count keeps python ints / None).
OUT_NUMERIC_COLUMNS: list[str] = OUT_FLOAT_COLUMNS + ["rv_bar_count"]
