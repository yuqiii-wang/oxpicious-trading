"""builds.futures.config — CFFEX futures product code mappings and column definitions.

CFFEX product codes:
  Index futures (股指):
    IC  — 中证500股指期货 (CSI 500 Index Futures)
    IF  — 沪深300股指期货 (CSI 300 Index Futures)
    IH  — 上证50股指期货 (SSE 50 Index Futures)
    IM  — 中证1000股指期货 (CSI 1000 Index Futures)
  Bond futures (国债):
    T   — 10年期国债期货 (10-Year Treasury Bond Futures)
    TF  — 5年期国债期货 (5-Year Treasury Bond Futures)
    TL  — 30年期国债期货 (30-Year Treasury Bond Futures)
    TS  — 2年期国债期货 (2-Year Treasury Bond Futures)

Contract code format: <PRODUCT><YYMM>, e.g. IC2607 = CSI500 July-2026.
"""
from __future__ import annotations

import datetime as _dt

# Product code → Chinese product name
PRODUCT_NAMES: dict[str, str] = {
    "IC": "中证500股指期货",
    "IF": "沪深300股指期货",
    "IH": "上证50股指期货",
    "IM": "中证1000股指期货",
    "T":  "10年期国债期货",
    "TF": "5年期国债期货",
    "TL": "30年期国债期货",
    "TS": "2年期国债期货",
}

# Product code → contract type
PRODUCT_TYPES: dict[str, str] = {
    "IC": "index",
    "IF": "index",
    "IH": "index",
    "IM": "index",
    "T":  "bond",
    "TF": "bond",
    "TL": "bond",
    "TS": "bond",
}

# Product codes (ordered)
PRODUCT_CODES: tuple[str, ...] = ("IC", "IF", "IH", "IM", "T", "TF", "TL", "TS")

# Product → (underlying_code, underlying_name) mapping
# Index futures map to stock index codes (000300, 000905, 000016, 000852)
# Bond futures use synthetic bond-code identifiers
PRODUCT_UNDERLYING: dict[str, tuple[str, str]] = {
    "IC": ("000905", "中证500"),
    "IF": ("000300", "沪深300"),
    "IH": ("000016", "上证50"),
    "IM": ("000852", "中证1000"),
    "T":  ("T10",  "10年期国债"),
    "TF": ("TF5",  "5年期国债"),
    "TL": ("TL30", "30年期国债"),
    "TS": ("TS2",  "2年期国债"),
}

# Minimum length of a contract code: T2609 = 5 (1+4), IC2607 = 6 (2+4)
_MIN_CODE_LEN = 5

# CSV column → DataFrame column mapping
COL_MAP: dict[str, str] = {
    "合约代码":     "code",
    "今开盘":       "open",
    "最高价":       "high",
    "最低价":       "low",
    "成交量":       "trading_shares",
    "成交金额":     "trading_amount",
    "持仓量":       "open_interest",
    "持仓变化":     "open_interest_change",
    "今收盘":       "close",
    "今结算":       "settlement_price",
    "前结算":       "prev_settlement",
    "涨跌1":        "change",
    "涨跌2":        "change_pct",
    "Delta":        "delta",
}

# Numeric columns to convert
NUMERIC_COLS: tuple[str, ...] = (
    "open", "high", "low", "close",
    "settlement_price", "prev_settlement",
    "change", "change_pct",
    "trading_shares", "trading_amount",
    "open_interest", "open_interest_change",
    "delta",
)

# Invalid token values that map to NULL
_NULL_TOKENS: set[str] = {"", "--", "-", "—", "null", "NULL", "None", "nan", "NaN"}

# ---------------------------------------------------------------------------
# Contract code parsing
# ---------------------------------------------------------------------------

def parse_contract_code(code: str) -> tuple[str, str]:
    """Parse a CFFEX contract code into (product_code, contract_month).

    Examples:
      "IC2607" → ("IC", "2607")
      "T2609"  → ("T",  "2609")

    Returns (product_code, contract_month) or raises ValueError on invalid input.
    """
    s = str(code).strip()
    if len(s) < _MIN_CODE_LEN:
        raise ValueError(f"Contract code too short: '{code}'")
    # Try 2-letter product first (IC, IF, IH, IM, TF, TL, TS)
    product = s[:2]
    if product in PRODUCT_CODES:
        return product, s[2:]
    # Try 1-letter product (T)
    product = s[0]
    if product in PRODUCT_CODES:
        return product, s[1:]
    raise ValueError(f"Unknown product code in contract: '{code}'")


def normalize_contract_year_month(contract_month: str) -> str:
    """Convert YYMM to YYYY-MM.

    Examples:
      "2607" → "2026-07"
      "2503" → "2025-03"
    """
    ym = str(contract_month).strip()
    if len(ym) != 4:
        raise ValueError(f"Invalid YYMM format: '{contract_month}'")
    yy, mm = ym[:2], ym[2:]
    year = "20" + yy if len(yy) == 2 else yy
    return f"{year}-{mm}"


# ---------------------------------------------------------------------------
# Expiry date computation
# ---------------------------------------------------------------------------

_CFFEX_EXPIRY_WEEKDAY = 4  # Friday


def _nth_weekday(year: int, month: int, n: int, weekday: int) -> _dt.date:
    """Return the date of the nth weekday in a given month.

    Args:
        year: calendar year
        month: calendar month (1-12)
        n: which occurrence (1 = first, 2 = second, etc.)
        weekday: 0=Monday … 4=Friday … 6=Sunday

    Returns:
        datetime.date of the nth weekday
    """
    first = _dt.date(year, month, 1)
    day_offset = (weekday - first.weekday()) % 7
    first_occurrence = first + _dt.timedelta(days=day_offset)
    return first_occurrence + _dt.timedelta(weeks=n - 1)


def compute_expiry_date(
    contract_month: str,
    contract_type: str = "index",
) -> _dt.date:
    """Compute the CFFEX futures expiry date for a contract.

    CFFEX expiry rules:
      - Index futures (IC/IF/IH/IM): 3rd Friday of the contract month
      - Bond futures  (T/TF/TL/TS):  2nd Friday of the contract month

    Args:
        contract_month: YYMM string like "2607"
        contract_type: "index" or "bond" (determines which Friday)

    Returns:
        datetime.date of the expiry date
    """
    ym = str(contract_month).strip()
    yy, mm = int(ym[:2]), int(ym[2:])
    year = 2000 + yy
    month = mm

    n = 3 if contract_type == "index" else 2
    return _nth_weekday(year, month, n, _CFFEX_EXPIRY_WEEKDAY)