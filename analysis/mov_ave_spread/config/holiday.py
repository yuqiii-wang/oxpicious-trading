"""Holiday / non-trading-day risk constants for analysis.mov_ave_spread.

analysis.mov_ave_rsi_holiday — captures previous-day trading/weekend/
holiday status + today's intraday gaps. Internal step of the parent
mov_ave_spread pipeline (see steps/holiday.py).
"""
HOLIDAY_TABLE = "analysis.mov_ave_rsi_holiday"
HOLIDAY_ANALYSIS_NAME = "mov_ave_rsi_holiday"

HOLIDAY_DESCRIPTION = (
    "Non-trading-day risk analysis (ETF + Index + Stock). For each trading "
    "day D, captures whether the previous calendar day (D-1) was a trading "
    "day / weekend / holiday / long holiday (>= 3 consecutive non-trading "
    "days including at least one official holiday), the consecutive "
    "non-trading-day count ending on D-1, and today's intraday high-low "
    "gap ((high-low)/close) and open-close gap ((close-open)/open). "
    "Source: same DataFrame as the mov_ave_spread parent pipeline "
    "(price, open, high, low columns). Holiday classification uses "
    "the project calendar (_common._holidays_and_weekdays). The sec_type "
    "column discriminates the source universe ('etf' | 'index' | 'stock')."
)

# All holiday column names in order.
HOLIDAY_COLUMNS = (
    "sec_type", "code", "date",
    "is_prev_day_trading",
    "is_prev_day_weekend",
    "is_prev_day_holiday",
    "is_prev_day_long_holiday",
    "non_trading_day_count",
    "today_high_low_gap",
    "today_open_close_gap",
)
