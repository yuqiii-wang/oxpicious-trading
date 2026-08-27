"""Configuration constants for builds.industry.

Industry Basic Stats (Baseline) — composite index OHLC per industry.

Populates stats.industry_basic_stats with the cross-sectional aggregation of
rebased-to-100 index OHLC across member indices within each industry,
bucketed by pool_size. Renamed from analysis.industry_sentiments
(2026-08-24); mean_price was rehooked to mean_close and mean_open/high/low
were added (all four share the per-index scale factor 100 / first close).

PK: (industry_id, date, pool_size)
  pool_size in ('small','mid','large','all')
    small = stock_num < 51   (tight thematic indices, e.g. 中证银行 50)
    mid   = stock_num <= 180 (mid-cap baskets, e.g. CSI 100/200)
    large = otherwise         (broad baskets, e.g. CSI 300/500/800/1000)
    all   = every member index regardless of pool size

AGGREGATES PER (date, industry_id, pool_size):
  mean_open / mean_high / mean_low / mean_close
                      = AVG(rebased_to_100 OHLC) — the composite index OHLC.
                        Single per-index scale factor 100 / first available
                        close applied to all four fields.
  var_price           = VARIANCE(rebased_to_100 close)  (kept from the former
                        industry_sentiments schema)
  mean_pe             = AVG(raw PE) (NULL PE excluded)
  total_trading_amount = SUM(stock_liquidity_margin.trading_amount) across the
                        UNION of stocks from all member indices' active
                        compositions (each stock counted once).
"""
TABLE = "stats.industry_basic_stats"

# Pool-size buckets materialized. 'all' includes every compositioned
# member index regardless of bucket.
POOL_SIZES = ["small", "mid", "large", "all"]
