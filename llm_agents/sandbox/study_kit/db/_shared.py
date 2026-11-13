"""study_kit.db._shared — the private plumbing shared by the query-layer
files (one public function per module): the psycopg connection, the
result-column coercion, and the sec_type maps. Import from the function
modules, not from here.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import psycopg

# sec_type → the reconstructed daily view (keys (date, code)).
DAILY_VIEWS: dict[str, str] = {
    "index": "stats.v_index_daily",
    "etf": "stats.v_etf_margin",
    "stock": "stats.v_stock_daily",
    "futures": "stats.v_futures_daily",
}

# sec_type label → its static.label_dim 'sec_type' SMALLINT code
# (0=etf 1=index 2=stock 3=industry — the code analysis.* tables store).
SEC_TYPE_CODES: dict[str, int] = {
    "etf": 0, "index": 1, "stock": 2, "industry": 3,
}

# The daily projection per view — the same canonical names out of every
# sec_type (pct_change is change_pct on the index/futures views; the ETF
# view adds adjusted prices). trading_amount is 成交额 (yuan) everywhere.
_DAILY_PROJECTIONS: dict[str, str] = {
    "index": """date, code, name, open, high, low, close,
                change_pct AS pct_change, trading_shares, trading_amount,
                ma5, ma20, ma60, ma120, ma252, ema12, ema26""",
    "etf": """date, code, name, open, high, low, close, adj_close,
              pct_change, trading_shares, trading_amount,
              ma5, ma20, ma60, ma120, ma252, ema12, ema26""",
    "stock": """date, code, name, open, high, low, close,
                pct_change, trading_shares, trading_amount,
                ma5, ma20, ma60, ma120, ma252, ema12, ema26""",
    "futures": """date, code, name, open, high, low, close,
                  change_pct AS pct_change, trading_shares, trading_amount,
                  open_interest""",
}

# Columns kept as-is (dates / identifiers); everything else numeric.
_TEXT_COLUMNS = frozenset({"date", "code", "name"})


def connect() -> psycopg.Connection:
    """One read-only psycopg connection from the preset PG* env."""
    return psycopg.connect()


def coerce_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Decimal/None result columns → float (dates and identifiers stay)."""
    for col in df.columns:
        if col in _TEXT_COLUMNS or col.endswith("_verdict"):
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().any() or len(df) == 0:
            df[col] = converted
    return df


def require_sec_type(sec_type: str, supported: dict[str, Any]) -> Any:
    """Map a sec_type label to its per-type value or raise the kit's
    informative error (kept identical across fetch modules)."""
    if sec_type not in supported:
        raise ValueError(
            f"sec_type {sec_type!r}: expected one of {sorted(supported)}")
    return supported[sec_type]
