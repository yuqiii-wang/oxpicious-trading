"""analyze.futures — Futures basis and correlation analysis.

Computes per-(date, code) metrics that compare each futures contract's
price against its underlying:

  - gap_price_vs_underlying:  (futures_close − underlying_price) / underlying_price
  - gap_price_ma5_vs_underlying_ma5: same on the 5-day MA
  - gap_changing_rate_price_vs_underlying: 1st-order derivative of the gap
    (day-over-day diff) — reflects whether the basis is continuing to
    converge (negative) or diverge (positive)
  - gap_changing_rate_price_ma5_vs_underlying_ma5: same on the MA5 gap
  - corr_price_vs_underlying: 20-day rolling correlation(futures_close, underlying_price)
  - corr_price_ma5_vs_underlying_ma5: 20-day rolling correlation(futures_ma5, underlying_ma5)

Underlying sources:
  - Index futures (IC/IF/IH/IM) → stats.index_basic_stats.close + index_tech_stats.ma5
  - Bond futures  (T/TF/TL/TS)  → stats.debt_treasury yield curve, converted to
    a zero-coupon bond price proxy via  price = 100 / (1 + y/2)^(2·tenor_years)

  - gap_max_price_vs_underlying_over_20days: rolling max of the basis
    over the trailing 20 trading days (monthly extreme)
  - gap_max_price_vs_underlying_over_60days: same over 60 days (quarterly)

Output table: analysis.futures_ext
"""
