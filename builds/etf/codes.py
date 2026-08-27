"""ETF helpers (money-market / bond ETF filter)."""


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
