-- ============================================================================
--  analysis_insights.insight_templates seed — 'Composites' tab (composites).
--
--  Purpose-scoped slice of the registry (the sibling 01_ DDL carries the
--  table + the resolution contract). This file is the TAB'S source of
--  truth: applying it re-syncs the tab (DELETE its rows, INSERT these).
--  Rows are ordered by TRADING VALUE within each page (ordinal = the
--  Ask AI modal's display order — the most decision-relevant metric
--  first); template_id = analysis.composites.<page>.<metric>.
--
--  DESCRIPTION STYLE (2026-10-27): every description is an AI-prompt
--  fragment — financial meaning, how to act, thresholds with rarity /
--  attention parentheses. No UI wording.
-- ============================================================================

DELETE FROM analysis_insights.insight_templates WHERE tab = 'composites';

INSERT INTO analysis_insights.insight_templates
    (template_id, tab, page, metric, label, unit, description, spec, ordinal)
VALUES
    -- offset-correlations · Offset correlations (opposite-industry) [scope=industry]
    ('analysis.composites.offset-correlations.opposite_score_60', 'composites', 'offset-correlations', 'opposite_score_60', 'Opposite score 60d', '', '(1 - offset_sub_corr)/2 of the industry''s BEST 60d window (vs 000300, market factor removed). 1.0 = perfectly opposite the benchmark; > 0.5 is the hedge-grade anti-correlation threshold the pair-hedge universe requires (a minority of industries — rare, high attention).', '{"scope": "industry", "page_title": "Offset correlations (opposite-industry)", "source": {"kind": "table", "table": "analysis_composites.industry_corr_benchmark_offsets", "value": "opposite_score_ma60_60d", "date": "start_date", "code": "industry_id", "match": {"benchmark_code": "000300", "interval": 20, "pool_size": 3}, "order": {"by": "value", "dir": "desc"}}}'::jsonb, 0),
    ('analysis.composites.offset-correlations.offset_sub_corr_60', 'composites', 'offset-correlations', 'offset_sub_corr_60', 'Offset corr 60d', '', 'Market-subtracted 60d correlation of the same best window — the raw corr behind the opposite score; near -1 = true anti-correlation (the hedge actually offsets), near 0 = just uncorrelated (no offset benefit).', '{"scope": "industry", "page_title": "Offset correlations (opposite-industry)", "source": {"kind": "table", "table": "analysis_composites.industry_corr_benchmark_offsets", "value": "offset_sub_corr_ma60_60d", "date": "start_date", "code": "industry_id", "match": {"benchmark_code": "000300", "interval": 20, "pool_size": 3}, "order": {"by": "value", "dir": "asc"}}}'::jsonb, 1),

    -- pair-hedge-ratio · Pair hedge ratio vs benchmark industry [scope=industry]
    ('analysis.composites.pair-hedge-ratio.hedge_raw_20d', 'composites', 'pair-hedge-ratio', 'hedge_raw_20d', 'Hedge raw 20d', '', 'Minimum-variance hedge ratio h* (raw mode, 20d window) vs the latest benchmark pair leg — short that many units of the leg per unit of the industry for the lowest-variance pair.', '{"scope": "industry", "page_title": "Pair hedge ratio vs benchmark industry", "source": {"kind": "table", "table": "analysis.pair_pct_change_ratios", "value": "hedge_raw_ma20d", "date": "date", "code": "code", "match": {"benchmark_code": "$latest", "benchmark_market": "000300", "interval": 20, "grain": 1}}}'::jsonb, 0),
    ('analysis.composites.pair-hedge-ratio.hedge_offset_20d', 'composites', 'pair-hedge-ratio', 'hedge_offset_20d', 'Hedge offset 20d', '', 'h* computed on market-offset series (common factor removed, 20d window) — the idiosyncratic-only hedge weight; prefer it when the intent is pure relative value, not beta timing.', '{"scope": "industry", "page_title": "Pair hedge ratio vs benchmark industry", "source": {"kind": "table", "table": "analysis.pair_pct_change_ratios", "value": "hedge_offset_ma20d", "date": "date", "code": "code", "match": {"benchmark_code": "$latest", "benchmark_market": "000300", "interval": 20, "grain": 1}}}'::jsonb, 1),

    -- benchmark-price · Benchmark price [scope=market]
    ('analysis.composites.benchmark-price.close', 'composites', 'benchmark-price', 'close', 'Close', '', 'Benchmark index close at the as-of date (the ask''s instrument).', '{"scope": "market", "page_title": "Benchmark price", "source": {"kind": "compute", "fn": "close"}}'::jsonb, 0),
    ('analysis.composites.benchmark-price.chg_20d', 'composites', 'benchmark-price', 'chg_20d', '20d change', '%', '20-session close change — the market regime context every composite read sits in.', '{"scope": "market", "page_title": "Benchmark price", "source": {"kind": "compute", "fn": "pct_change", "args": {"days": 20}}}'::jsonb, 1),
    ('analysis.composites.benchmark-price.pe', 'composites', 'benchmark-price', 'pe', 'PE', '', 'Raw PE at the as-of date (index benchmarks) — the valuation level the price regime trades on.', '{"scope": "market", "page_title": "Benchmark price", "source": {"kind": "table", "table": "analysis.pe", "value": "pe", "date": "date", "code": "code", "sec_type": "sec_type"}, "sec_types": ["index"]}'::jsonb, 2);
