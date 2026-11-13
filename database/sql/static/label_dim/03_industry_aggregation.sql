-- ============================================================================
--  static.label_dim seed — INDUSTRY AGGREGATION vocabularies
--
--  How constituents are aggregated / ranked / pooled in the industry
--  analytics. Owning tables: analysis.industry_hypes_and_drains /
--  _seasonal (weighting, rank_side), stats.industry_basic_stats,
--  analysis.industry_correlations / industry_etf_contribution,
--  analysis_composites.industry_corr_benchmark_offsets (pool_size),
--  analysis.pair_pct_change_ratios (pair_grain),
--  analysis.industry_stock_change_dist (stock_change_bucket);
--  attribution_type is PENDING (analysis.industry_attributions).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- weighting: industry aggregation family (equal-weight vs
    -- trading_amount-weighted)
    ('weighting',  0, 'equal',  0),
    ('weighting',  1, 'amt',    1),
    -- rank_side: hypes-and-drains ranking direction
    ('rank_side',  0, 'HYPE',   0),
    ('rank_side',  1, 'DRAIN',   1),
    -- pool_size: code order is SEMANTIC (small -> mid -> large -> all);
    -- sort_order keeps the pre-refactor TEXT presentation order
    -- (alphabetical) so API/UI listings are unchanged
    ('pool_size',  0, 'small',  3),
    ('pool_size',  1, 'mid',    2),
    ('pool_size',  2, 'large',  1),
    ('pool_size',  3, 'all',    0),
    -- attribution_type: industry attributions (PENDING)
    ('attribution_type', 0, 'equal',      0),
    ('attribution_type', 1, 'trading_amt', 1),
    -- pair_grain: subject grain of analysis.pair_pct_change_ratios rows —
    -- per-index pair rows vs the industry aggregate derived from them
    -- (AVG over the industry's member-index rows; the cross_stats
    -- industry-grain pattern)
    ('pair_grain', 0, 'index',    0),
    ('pair_grain', 1, 'industry', 1),
    -- stock_change_bucket: the daily pct-change distribution bucket of
    -- analysis.industry_stock_change_dist (the Stock Attribution audit —
    -- per (industry, date) the number of member stocks in each bucket).
    -- Bucket k = [edge[k-1], edge[k]) over edges -17.5/-12.5/-7.5/-5/
    -- -2.5/0/2.5/5/7.5/12.5/17.5 (percent); code order is SEMANTIC
    -- (most-negative tail -> most-positive tail) and doubles as the
    -- presentation order. pct_change == 0 falls in bucket 6 ('0~2.5%').
    ('stock_change_bucket',  0, '<-17.5%',      0),
    ('stock_change_bucket',  1, '-17.5~-12.5%', 1),
    ('stock_change_bucket',  2, '-12.5~-7.5%',  2),
    ('stock_change_bucket',  3, '-7.5~-5%',     3),
    ('stock_change_bucket',  4, '-5~-2.5%',     4),
    ('stock_change_bucket',  5, '-2.5~0%',      5),
    ('stock_change_bucket',  6, '0~2.5%',       6),
    ('stock_change_bucket',  7, '2.5~5%',       7),
    ('stock_change_bucket',  8, '5~7.5%',       8),
    ('stock_change_bucket',  9, '7.5~12.5%',    9),
    ('stock_change_bucket', 10, '12.5~17.5%',  10),
    ('stock_change_bucket', 11, '>=17.5%',     11);
