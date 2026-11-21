"""Row contracts for the oracle pipeline (class-first per
oxpicious-dev-conventions — pydantic carriers crossing module
boundaries; they never ride inside frame compute, materializing only
at the write boundary)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class OracleDecisionRow(BaseModel):
    """One analysis.oracle_signals row (grain: sec_type + code + date,
    date = the PIVOT date — the local extreme itself). Mirrors
    database/sql/analysis/oracle_signals.sql; the pipeline's OUT_*
    column lists derive from the field order so the frame->row
    contract stays single-sourced. The ext_* legs carry the row's
    hindsight SWING TARGET — the next pivot of the opposite type
    (NULL at the frontier, where that opposite pivot is not confirmed
    yet)."""

    sec_type: int  # static.label_dim 'sec_type' SMALLINT code (1 = index)
    code: str
    date: object   # datetime.date at the write boundary

    # static.label_dim 'oracle_action' SMALLINT code: 0 buy (local min)
    # 1 sell (local max) — every pivot carries an action in v2
    action: int

    # the 5-bar pivot window (WINDOW_SIDES = 2 rows each side —
    # close[i] is the extreme of [i-2, i+2]); also the one-action span
    # PER SIDE (any 5 consecutive rows hold at most one buy AND at
    # most one sell — a buy and a sell may sit within the same 5 rows)
    window_days: int

    # the NEXT OPPOSITE pivot — the row's hindsight swing target
    # (PLACED-level: points at the next opposite placed pivot even
    # when that pivot's own row was omitted by the grading floor)
    ext_date: object = None     # datetime.date at the write boundary
    ext_offset: Optional[int] = None  # traded rows pivot -> opposite
    ext_price: Optional[float] = None  # the opposite pivot's close

    # the FAVORABLE-MOVE GRADE (v5, 0..100): the weighted sum
    # 0.3 * backward-leg + 0.7 * forward-leg, each leg the linear
    # ramp of its favorable move from CONF_NO_ACTION_MOVE (2%) to
    # CONF_FULL_MOVE (10%). A pivot first passes the v7
    # GENUINE-EXTREME gate (buy: no close 5%+ below within the next
    # 30 rows and no 5%+ rise from the prior 30 rows' floor; sell: no
    # 5%+ fall from the prior 20 rows' ceiling and no close 5%+ above
    # within the next 20 rows — no row on any leg's failure); a
    # realized pivot whose both legs miss the floor OMITS the row
    # entirely, and an UNREALIZED window whose backward leg also
    # misses the floor (frontier pivots, < 20 future rows) emits
    # NULL — unknown is not small — re-graded by later runs'
    # revisable tail
    confidence: Optional[float] = None


# The DB column order (the frame's write boundary contract).
OUT_COLUMNS: list[str] = list(OracleDecisionRow.model_fields.keys())

# float64 frame columns (the price leg + the nullable confidence grade).
OUT_FLOAT_COLUMNS: list[str] = ["ext_price", "confidence"]

# Columns handed to sanitize_for_db_insert's numeric branch (floats
# round/NaN-swept; the two NULLABLE smallint legs keep python ints /
# None through sanitize's int branch — the rv_bar_count precedent).
OUT_NUMERIC_COLUMNS: list[str] = OUT_FLOAT_COLUMNS + [
    "window_days", "ext_offset",
]
