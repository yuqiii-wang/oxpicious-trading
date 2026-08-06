"""Configuration constants for analyze.industry_sentiments.

Industry Sentiments (rebased-to-100 levels).

Populates analysis.industry_sentiments with cross-sectional aggregation of
rebased-to-100 index values across member indices within each industry,
bucketed by pool_size.

PK: (date, industry_id, pool_size)
  pool_size in ('small','mid','large','all')
    small = stock_num < 51   (tight thematic indices, e.g. 中证银行 50)
    mid   = stock_num <= 180 (mid-cap baskets, e.g. CSI 100/200)
    large = otherwise         (broad baskets, e.g. CSI 300/500/800/1000)
    all   = every member index regardless of pool size

AGGREGATES PER (date, industry_id, pool_size):
  mean_price        = AVG(rebased_to_100 close)
  var_price         = VARIANCE(rebased_to_100 close)
  mean_pe           = AVG(raw PE) (NULL PE excluded)
  total_trading_amount = SUM(stock_liquidity_margin.trading_amount) across the
                      UNION of stocks from all member indices' active
                      compositions (each stock counted once).
"""
TABLE = "analysis.industry_sentiments"
ANALYSIS_NAME = "industry_sentiments"
ANALYSIS_DESCRIPTION = (
    "Industry sentiment cross-section: one row per (date, industry_id, "
    "pool_size). Aggregates index values across member indices "
    "(stats.sec_classification type='index' AND industry_id matches AND "
    "index has composition data in stats.sec_composition source_type='index') "
    "in the named pool_size slice. Indices WITHOUT composition data are "
    "excluded entirely. mean_price/var_price: rebased-to-100 at each index's "
    "first available close (history start). mean_pe: raw PE from "
    "stats.index_valuation. total_trading_amount: SUM of stock_liquidity_margin.trading_amount "
    "across the UNION of stocks from member indices' compositions (LATEST "
    "snapshot per code, no temporal filter — same stock universe for all "
    "dates; yuan). pool_size: small (stock_num < 51), mid (51-180), large "
    "(> 180), all (every compositioned member). stock_num and composition "
    "use the LATEST sec_composition snapshot per code for ALL dates "
    "(temporal extrapolation — current composition as proxy for historical). "
    "Broad-market industries BROAD_CSI/BROAD_SSE/BROAD_SZSE/BROAD_STAR "
    "aggregated identically. Built by analyze.industry_sentiments "
    "(truncate-then-recompute)."
)

# Pool-size buckets materialized. 'all' includes every compositioned
# member index regardless of bucket.
POOL_SIZES = ["small", "mid", "large", "all"]
