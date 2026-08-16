"""ETF code classification helpers (SZSE vs SSE, money-market filter)."""
from builds.etf.paths import SZSE_ETF_PREFIXES, SSE_ETF_PREFIXES


def is_szse_etf_code(code):
    s = str(code).strip()
    if "." in s:
        s = s.split(".")[0]
    try:
        s = str(int(float(s))).zfill(6)
    except Exception:
        pass
    return len(s) == 6 and s.isdigit() and s[:2] in SZSE_ETF_PREFIXES


def is_sse_etf_code(code):
    s = str(code).strip()
    if "." in s:
        s = s.split(".")[0]
    try:
        s = str(int(float(s))).zfill(6)
    except Exception:
        pass
    return len(s) == 6 and s.isdigit() and any(s.startswith(p) for p in SSE_ETF_PREFIXES)


def get_exchange_for_etf(code):
    if is_szse_etf_code(code):
        return "SZ"
    if is_sse_etf_code(code):
        return "SS"
    return None


# ---------------------------------------------------------------------------
# Money-market / bond ETF filter (excluded from OHLCV processing)
# ---------------------------------------------------------------------------
MONEY_MARKET_KW = (
    "货币", "快线", "快钱", "现金宝", "添利", "理财",
    "债券", "债基", "短融", "国债", "信用", "利率", "纯债",
    "稳健", "增益", "固定",
    "国开", "政金", "地债", "地方债", "进出口", "农发",
)


def is_money_market_etf(name):
    s = str(name)
    return any(k in s for k in MONEY_MARKET_KW)
