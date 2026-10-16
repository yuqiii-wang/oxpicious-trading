-- ============================================================================
--  static.label_dim seed — INDUSTRY AGGREGATION vocabularies
--
--  How constituents are aggregated / ranked / pooled in the industry
--  analytics. Owning tables: analysis.industry_hypes_and_drains /
--  _seasonal (weighting, rank_side), stats.industry_basic_stats,
--  analysis.industry_correlations / industry_etf_contribution,
--  analysis_composites.industry_corr_benchmark_offsets (pool_size);
--  attribution_type is PENDING (analysis.industry_attributions).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- weighting: industry aggregation family (equal-weight vs
    -- trading_amount-weighted)
    ('weighting',  0, 'equal',  0),
    ('weighting',  1, 'amt',    1),
    -- rank_side: hypes-and-drains ranking direction
    ('rank_side',  0, 'HYPE',   0),
    ('rank_side',  1, 'DRAIN',  1),
    -- pool_size: code order is SEMANTIC (small -> mid -> large -> all);
    -- sort_order keeps the pre-refactor TEXT presentation order
    -- (alphabetical) so API/UI listings are unchanged
    ('pool_size',  0, 'small',  3),
    ('pool_size',  1, 'mid',    2),
    ('pool_size',  2, 'large',  1),
    ('pool_size',  3, 'all',    0),
    -- attribution_type: industry attributions (PENDING)
    ('attribution_type', 0, 'equal',      0),
    ('attribution_type', 1, 'trading_amt', 1);
