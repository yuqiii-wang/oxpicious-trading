"""builds.options.cffex.config — CFFEX options product mappings and column definitions.

CFFEX index option products (指数期权):
  IO  — 沪深300指数期权 (CSI 300 Index Options)       underlying: 000300
  HO  — 上证50指数期权 (SSE 50 Index Options)         underlying: 000016
  MO  — 中证1000指数期权 (CSI 1000 Index Options)     underlying: 000852
  CO  — 中证500指数期权 (CSI 500 Index Options)       underlying: 000905

CFFEX stock option products (股票期权, ETF options on CFFEX):
  HO  — 上证50ETF期权 (SSE 50 ETF Options)  underlying: 510050
  IO  — 沪深300ETF期权 (CSI 300 ETF Options) underlying: 510300

Contract code format: <PRODUCT><YYMM>-<C|P>-<STRIKE>
  e.g. IO2607-C-4000 → product=IO, month=2607, type=C(CALL), strike=4000

CFFEX index options expire on the 3rd Friday of the expiry month
(最后交易日/到期日 = 合约到期月份的第三个星期五, 遇法定假日顺延).
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Product code definitions
# ---------------------------------------------------------------------------

# Option product code → Chinese product name
PRODUCT_NAMES: dict[str, str] = {
    "IO": "沪深300指数期权",
    "HO": "上证50指数期权",
    "MO": "中证1000指数期权",
    "CO": "中证500指数期权",
}

# Option product code → contract type
PRODUCT_TYPES: dict[str, str] = {
    "IO": "index",
    "HO": "index",
    "MO": "index",
    "CO": "index",
}

# Option product codes (ordered)
PRODUCT_CODES: tuple[str, ...] = ("IO", "HO", "MO", "CO")

# Product → (underlying_code, underlying_name) mapping
# Index options map to stock index codes (000300, 000016, etc.)
PRODUCT_UNDERLYING: dict[str, tuple[str, str]] = {
    "IO": ("000300", "沪深300"),
    "HO": ("000016", "上证50"),
    "MO": ("000852", "中证1000"),
    "CO": ("000905", "中证500"),
}

# Minimum contract code length: IO2607-C-4000 = 14 chars
# After parsing product (2 chars) we need at least 12 more chars
_MIN_CODE_LEN = 12

# ---------------------------------------------------------------------------
# CSV column mapping
# ---------------------------------------------------------------------------

# CSV column → internal DataFrame column
COL_MAP: dict[str, str] = {
    "合约代码":     "contract_code",
    "今开盘":       "open",
    "最高价":       "high",
    "最低价":       "low",
    "成交量":       "volume",
    "成交金额":     "trading_amount",
    "持仓量":       "open_interest",
    "持仓变化":     "open_interest_change",
    "今收盘":       "close",
    "今结算":       "settle",
    "前结算":       "prev_settle",
    "涨跌1":        "change",
    "涨跌2":        "change_pct",
    "Delta":        "delta",
}

# Numeric columns to convert
NUMERIC_COLS: tuple[str, ...] = (
    "open", "high", "low", "close",
    "settle", "prev_settle",
    "change", "change_pct",
    "volume", "trading_amount",
    "open_interest", "open_interest_change",
    "delta",
)

# ---------------------------------------------------------------------------
# Contract code parsing
# ---------------------------------------------------------------------------

# Pattern: PRODUCT + YYMM + '-' + (C|P) + '-' + STRIKE
# Examples: IO2607-C-4000, HO2607-P-2500, MO2703-C-8400
_CONTRACT_CODE_RE = re.compile(
    r"^(IO|HO|MO|CO)(\d{4})-([CP])-(\d+(?:\.\d+)?)$"
)


def parse_contract_code(code: str) -> dict:
    """Parse a CFFEX option contract code into its components.

    Examples:
      "IO2607-C-4000" → {product:"IO", month:"2607", type:"CALL", strike:4000}
      "HO2607-P-2500" → {product:"HO", month:"2607", type:"PUT", strike:2500}

    Returns a dict with keys: product, month, option_type, strike, or
    raises ValueError on invalid input.
    """
    s = str(code).strip()
    m = _CONTRACT_CODE_RE.match(s)
    if not m:
        raise ValueError(f"Invalid CFFEX option contract code: '{code}'")

    product = m.group(1)
    month = m.group(2)
    cp = m.group(3)
    strike_str = m.group(4)

    # Convert strike to float (option prices can have decimals)
    try:
        strike = float(strike_str)
    except ValueError:
        raise ValueError(f"Invalid strike price in contract code: '{code}'")

    return {
        "product": product,
        "month": month,
        "option_type": "CALL" if cp == "C" else "PUT",
        "strike": strike,
        "strike_str": strike_str,
    }


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
# Expiry date calculation
# ---------------------------------------------------------------------------

def _third_friday(year: int, month: int) -> int:
    """Return the day of the 3rd Friday of a given year-month.

    CFFEX index options expire on the 3rd Friday of the expiry month
    (last trading day = expiry day; postponed to the next trading day
    when it falls on a public holiday — not calendar-adjusted here).
    """
    import datetime
    # First day of the month
    first = datetime.date(year, month, 1)
    # Friday = weekday 4 (Monday=0, Sunday=6)
    days_until_first_friday = (4 - first.weekday()) % 7
    first_friday = first.day + days_until_first_friday
    third_friday = first_friday + 14  # 2 more weeks
    return third_friday


def compute_expiry_date(trade_date, contract_month: str):
    """Compute the expiration date for a CFFEX option contract.

    CFFEX index options expire on the 3rd Friday of the expiry month.

    Args:
        trade_date: datetime.date of the trading day
        contract_month: YYMM string like "2607"

    Returns:
        datetime.date of the expiry date
    """
    ym = str(contract_month).strip()
    yy, mm = int(ym[:2]), int(ym[2:])
    year = 2000 + yy
    month = mm

    expiry_day = _third_friday(year, month)
    return type(trade_date)(year, month, expiry_day)