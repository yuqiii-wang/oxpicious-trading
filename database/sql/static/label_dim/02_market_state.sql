-- ============================================================================
--  static.label_dim seed — MARKET STATE vocabularies
--
--  What the market / a price leg is doing: the regime split, the
--  indicator-band side, and the price-vs-amt signed price speed and
--  volume state. Owning tables: stats.market_regimes /
--  market_regime_spans, analysis.mov_ave_price_vs_amt, and the signal
--  tables (side = the curve/threshold bucket side).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- regime: code order == builds.market_regimes REGIMES tuple ==
    -- lexicographic label order (ORDER BY code == ORDER BY label)
    ('regime',     0, 'calm',   0),
    ('regime',     1, 'hot',    1),
    ('regime',     2, 'panic',  2),
    ('regime',     3, 'quiet',  3),
    -- 4 = the POOLED population (no regime split): the per-key curve
    -- fitted over ALL the cell's triggers regardless of regime, and —
    -- since 2026-10-12 — every ma_cross row (thresholds + history too:
    -- the family is regime-FREE, each config registers exactly ONE
    -- pooled bucket)
    ('regime',     4, 'all',    4),
    -- side: the bucket's own side of the indicator
    ('side',       0, 'top',    0),
    ('side',       1, 'bottom', 1),
    -- side (extended): flat is the price-vs-amt registry's mid band only
    ('side',       2, 'flat',   2),
    -- px_speed: price-vs-amt price leg, signed semantic order
    ('px_speed',   0, 'sharp_dn', 0),
    ('px_speed',   1, 'slow_dn',  1),
    ('px_speed',   2, 'flat',     2),
    ('px_speed',   3, 'slow_up',  3),
    ('px_speed',   4, 'sharp_up', 4),
    -- vol_state: price-vs-amt volume leg
    ('vol_state',  0, 'shrink', 0),
    ('vol_state',  1, 'normal', 1),
    ('vol_state',  2, 'heavy',  2),
    -- roof_floor_method: how a roof/floor (resistance/support) level is
    -- quantitatively estimated — analysis.mov_ave_spreads_roof_floor_
    -- estimates.method. Registry order = the pipeline's method registry
    -- (roof_floor_estimate/methods); 5 median is the cross-method
    -- consensus row (median of 0..4), kept in the same domain so scans
    -- stay single-table.
    ('roof_floor_method', 0, 'trendline', 0),
    ('roof_floor_method', 1, 'donchian',  1),
    ('roof_floor_method', 2, 'quantile',  2),
    ('roof_floor_method', 3, 'channel',   3),
    ('roof_floor_method', 4, 'boll',      4),
    ('roof_floor_method', 5, 'median',    5),
    -- rf_rebound_outcome: analysis.mov_ave_spreads_roof_floor_rebounds.
    -- outcome — the rebound-audit branch of a recorded extreme (the
    -- 2026-10 retest study). Registry order = the audit's funnel order
    -- (no rebound -> never retested -> held/broken/unresolved).
    ('rf_rebound_outcome', 0, 'no_rebound',     0),
    ('rf_rebound_outcome', 1, 'never_retested', 1),
    ('rf_rebound_outcome', 2, 'held',           2),
    ('rf_rebound_outcome', 3, 'broken',         3),
    ('rf_rebound_outcome', 4, 'unresolved',     4);
