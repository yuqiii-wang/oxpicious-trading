"""Market Participation — one row per trading day for the whole A-share
market's participation gauges (analysis.market_participation; see
database/sql/analysis/31_market_participation.sql):

  turnover  — total SS+SZ proxy turnover from stats.exchange_trading_amt
              (each exchange proxied by ONE representative index), its
              MAs / vs-MA gaps / trailing-252 percentile;
  sse share — the RAW daily ss/(ss+sz) share (the piece
              analysis.mov_ave_trading_amt stores only smoothed), its
              MAs, vs-ma20 pp gap and trailing-20 OLS slope;
  breadth   — whole-universe advance/decline counts from
              stats.stock_basic_stats pct_change signs, A/D ratio,
              median/mean change, A/D MAs and trailing-252 percentile.

Serves the Market Beta page's "Market Participation" panel
(/analysis/commons/market-beta) via
GET /api/analysis/market-beta/participation.
"""
