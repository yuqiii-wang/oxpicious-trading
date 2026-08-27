"""builds._commons.column_maps — Centralized CSV column name mappings.

All build modules (stock, etf, index, bond, options) that read Chinese-named
CSV source files should import their column maps from here instead of
defining local copies. This guarantees consistent handling across sources
that may use slightly different header names for the same semantic field
(e.g. 融券余额 vs 融券余额(元), or 交易日期 vs 日期).

Categories:
  - STOCK_COL_MAP      — stock OHLCV CSV columns (shared SZSE/SSE/BSE schema)
  - STOCK_MARGIN_COL_MAP — stock margin detail CSV columns (SZSE + SSE)
  - ETF_COL_MAP        — ETF OHLCV CSV columns
  - ETF_MARGIN_COL_MAP — ETF margin detail CSV columns
  - INDEX_COL_MAP      — index OHLCV CSV columns (SSE archive, CSI)
  - BOND_COL_MAP       — bond yield curve CSV columns
  - _CANONICAL_SUFFIX  — canonical-code suffix (.SZ/.SS/.BJ/.SH) keyed by
                         market label, used when raw codes lack a suffix
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Stock OHLCV — shared schema across SZSE archive/trend, SSE trend, BSE trend,
# and SSE per-stock archive ({code}_trend.csv). All downloaders write these
# exact column names, and the build-side COL_MAP maps them to internal names.
# ---------------------------------------------------------------------------
STOCK_COL_MAP: dict[str, str] = {
    "交易日期":     "date",
    "证券代码":     "code",
    "证券简称":     "name",
    "前收":         "prev_close",
    "开盘":         "open",
    "最高":         "high",
    "最低":         "low",
    "今收":         "close",
    "涨跌幅（%）":  "pct_change",
    "成交量(万股)": "volume_wan",   # raw 万股 → converted to shares below
    "成交金额(万元)": "amount_wan", # raw 万元 → converted to yuan below
    "市盈率":       "pe",
}

# ---------------------------------------------------------------------------
# Stock margin detail — maps Chinese column names (from SZSE + SSE margin
# CSVs) to internal column names. SSE detail CSV does NOT publish:
#   - 融券余额(元)   → rq_balance_amt  (always 0 for SSE stocks)
#   - 融资融券余额(元) → total_balance  (always rz_balance for SSE stocks)
# so those columns are None/0 for SSE stocks and only populated for SZSE.
#
# Both exchanges publish:
#   融资买入额(元)    → rz_buy
#   融资余额(元)      → rz_balance
#   融券卖出量(股/份)  → rq_sell_qty
#   融券余量(股/份)    → rq_balance_qty
# ---------------------------------------------------------------------------
STOCK_MARGIN_COL_MAP: dict[str, str] = {
    "融资买入额(元)":   "rz_buy",
    "融资余额(元)":     "rz_balance",
    "融券卖出量(股/份)": "rq_sell_qty",
    "融券余量(股/份)":   "rq_balance_qty",
    "融券余额(元)":     "rq_balance_amt",
    "融资融券余额(元)":  "total_balance",
}

# SSE margin summary CSV also has "融券余量" (without unit suffix) and
# "融券余量金额(元)" instead of "融券余额(元)". These are mapped as aliases.
STOCK_MARGIN_COL_MAP_SSE_SUMMARY: dict[str, str] = {
    "融资买入额(元)":   "rz_buy",
    "融资余额(元)":     "rz_balance",
    "融券卖出量":       "rq_sell_qty",
    "融券余量":         "rq_balance_qty",
    "融券余量金额(元)": "rq_balance_amt",
    "融资融券余额(元)":  "total_balance",
}

# ---------------------------------------------------------------------------
# Canonical exchange suffix lookup — maps market label → suffix used when
# raw codes from legacy CSVs lack an exchange suffix.
# ---------------------------------------------------------------------------
CANONICAL_SUFFIX: dict[str, str] = {
    "深圳": ".SZ",
    "上海": ".SS",
    "北京": ".BJ",
    "SH": ".SH",
}

# ---------------------------------------------------------------------------
# Placeholder strings that mark invalid/missing CSV data.
# ---------------------------------------------------------------------------
PLACEHOLDER_MARKS: tuple[str, ...] = ("没有找到", "无数据")

# ---------------------------------------------------------------------------
# Valid date patterns for _safe_to_datetime pre-filtering.
# ---------------------------------------------------------------------------
DATE_VALID_RE = r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{8}$"
DATE_INVALID_PATTERNS: list[str] = ["没有找到符合条件的数据！", "无数据"]
