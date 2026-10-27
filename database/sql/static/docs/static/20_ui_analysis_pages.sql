-- ============================================================================
--  static.docs seed — analysis-page chart specs: PE, ETF holdings,
--  industry sentiments, composites, perf-attr, composition pies.
--  (Prefix re-sync per file: each DELETE below scopes its rows.)
-- ============================================================================

DELETE FROM static.docs WHERE doc_id LIKE 'ui.pe.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.etf-holdings.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.industry-sentiments.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.composites.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.perf-attr.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.composition-pie.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.industry-sentiments.page';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.etf-holdings.page';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.dividends.%';

INSERT INTO static.docs (doc_id, title, intro, series, notes, ordinal) VALUES
(
  'ui.industry-sentiments.page',
  NULL,
  'Industry sentiments for {{label}}: each member index''s daily close rebased to 100 at the visible window''s start, merged across the picked industries, with the server-precomputed mean ±1σ band (anchored at history start — aligned with the lines only at full range). Sub-plots: the cross-sectional mean PE and mean trading amount (亿元). Only indices with composition data appear; broad-market indices sit under the FIN sector.',
  NULL, NULL, 0
),
(
  'ui.etf-holdings.page',
  NULL,
  'ETF holdings for {{label}}: per-ETF quarterly holdings by industry — the 100%-stacked quarterly composition bars, the single-quarter industry pie, and the cross-quarter composition-change comparison.',
  NULL, NULL, 0
),
(
  'ui.dividends.page',
  NULL,
  'Dividends for {{label}}: the per-security dividends view on the shared baseline plot — ETF/stock charts mark ex-dividend dates on the price line. Same security universe as the PE page; no streaks card (the dividend_yield streak tables were removed).',
  NULL, NULL, 0
),
(
  'ui.pe.streaks',
  NULL,
  'Valuation band-break streaks: the daily PE against the trailing window''s static tightness zones. A streak is the maximal run of days above (high) / below (low) the window''s static band edge, bridging in-band gaps of ≤5 trading days. Long excursions mean the valuation has left its recent envelope into the extreme tail the tightness setting defines — historically an overextended regime that snaps back once the band re-anchors; the streak''s age and the PE''s distance from the band edge gauge how stretched the read is.',
  '[
    {"key": "pe", "name": "PE", "unit": "×", "description": "daily price/earnings multiple — the valuation level"},
    {"key": "highStreaks", "name": "High streaks", "description": "runs of days above the window''s static top band edge — the overvaluation regime"},
    {"key": "lowStreaks", "name": "Low streaks", "description": "runs of days below the window''s static bottom band edge — the undervaluation regime"},
    {"key": "selected", "name": "Selected streak", "description": "the highlighted streak — the span the question should focus on"}
  ]'::jsonb,
  '[
    {"key": "staticEdge", "text": "Break bands are detected against the anchor window''s STATIC edge; the DB archive streaks (tested against each month''s own moving band) are the registry counterpart."}
  ]'::jsonb,
  0
),
(
  'ui.etf-holdings.composition-bars',
  NULL,
  'Quarterly holdings of {{code}}{{#name}} ({{name}}){{/name}} by industry: one 100% stacked bar per quarter — each segment = industry weight / quarter total × 100. Read it as the fund''s factor exposure drift: a swelling slice = the manager rotating in (crowding into that industry''s factor), a shrinking one = trimmed exposure; sudden quarter-over-quarter jumps mark rebalancing or style rotation rather than drift. Composition data arrives quarterly — weight changes are positioning decisions, not daily flow.',
  NULL,
  '[
    {"key": "segMath", "text": "Each stacked segment = industry weight / quarter total × 100; quarters without a snapshot are absent (no carry-forward in the bars — the tooltip carry-forwards the last available pct)."},
    {"key": "colors", "text": "Colors are assigned by weight in the LATEST quarter (desc), then max weight across quarters."},
    {"key": "indexSource", "text": "The ETF itself has no holdings snapshots — composition comes from its tracking index {{indexCode}} ({{indexName}})."}
  ]'::jsonb,
  0
),
(
  'ui.etf-holdings.drilldown-dual',
  NULL,
  'Industry drill-down for {{industry}}: left = industry composite mean close (rebased to 100, all pools — stats.industry_basic_stats); right = the industry''s % of {{etf}}''s total composition (weight_pct / total_weight_pct × 100 per snapshot). Indication: rising close + swelling % = the ETF accumulating into strength — the bullish pairing; divergent pair = price without sponsorship.',
  '[
    {"key": "meanClose", "name": "{{industry}} mean_close (all pools)", "description": "industry composite mean close (rebased), all pools"},
    {"key": "inEtf", "name": "{{industry}} in {{etf}} (pct of total)", "unit": "%", "description": "industry weight as % of the ETF''s total composition (step line — carry-forward between snapshots)"}
  ]'::jsonb,
  '[
    {"key": "sparse", "text": "The ETF pct series is SPARSE (snapshot dates only) — the step line holds the last value until the next snapshot; the data carries the last available value with its as-of date."},
    {"key": "weightsIndex", "text": "Weights come from the ETF''s tracking index {{indexCode}} ({{indexName}})."},
    {"key": "weightsOwn", "text": "Weights come from the ETF''s own composition snapshots."}
  ]'::jsonb,
  0
),
(
  'ui.etf-holdings.drilldown-curves',
  NULL,
  'Member curves of {{industry}}: each member index rebased to 100 × P_t/P_own-first-close (same convention as stats.industry_basic_stats), so relative performance compares despite price-level disparities. Fanning curves = dispersion widening (a stock-picker''s tape — idiosyncratic moves dominating the industry); convergence = beta catch-up (the industry factor dominating).',
  NULL,
  '[
    {"key": "dynamicNames", "text": "Series names are the member index names; each curve is rebased to its OWN first close."}
  ]'::jsonb,
  0
),
(
  'ui.industry-sentiments.benchmark-price',
  NULL,
  'Benchmark {{benchmark}} daily price.{{#rolling}}Rebased mode: 100 × P_t/P_window-start so slopes compare cumulative performance; each selected industry overlays its contribution-excluded curve (benchmark − industry); the shaded gap = that industry''s added/removed return over the trailing rolling window. A swelling shade = the industry propels the benchmark; an ebbing shade = its pull decays.{{/rolling}}{{^rolling}}Absolute mode: raw daily closes on a shared price axis (benchmark + each selected industry''s ex-this-industry series).{{/rolling}}{{#showAmt}} Bars (right axis): benchmark turnover, 亿 CNY — the participation level behind the price move.{{/showAmt}}',
  '[
    {"key": "tradingAmt", "name": "Trading Amt", "unit": "亿 CNY", "description": "benchmark daily trading amount — the participation behind the move"}
  ]'::jsonb,
  '[
    {"key": "rollingWindow", "text": "Rolling window for the industry shades: {{windowLabel}} ({{rollingDays}} trading days)."}
  ]'::jsonb,
  0
),
(
  'ui.industry-sentiments.attribution',
  NULL,
  'Benchmark attribution for {{industry}}: per benchmark index, Contribution (left) = the industry''s share of that benchmark''s return; right axis pits the industry''s weight inside the benchmark (Benchmark Wt) against its broad-market weight (Industry Wt). Contribution ≫ weight ⇒ the industry over-drives that benchmark (its return is idiosyncratic, not membership beta); ≪ ⇒ it merely rides the membership.',
  '[
    {"key": "contribution", "name": "Contribution", "unit": "pp", "description": "return contribution to the benchmark index, percentage points"},
    {"key": "benchmarkWt", "name": "Benchmark Wt", "unit": "%", "description": "the industry''s weight inside the benchmark index"},
    {"key": "industryWt", "name": "Industry Wt", "unit": "%", "description": "the industry''s broad-market reference weight"}
  ]'::jsonb,
  '[
    {"key": "asOf", "text": "As-of date: {{date}}"}
  ]'::jsonb,
  0
),
(
  'ui.industry-sentiments.plot',
  NULL,
  'Industry sentiment for {{title}}: member closes rebased to 100 × P_t/P_0 on a shared date axis (pool scoped to small/mid/large members), broad-market benchmarks overlaid, with mean ±1σ dispersion bands and sub-plots for the industry''s mean PE and total trading amount (亿元). A tight band = a consensus tape (the industry factor dominates); wide σ = member dispersion (stock selection over beta); a benchmark outrunning the members = the industry lagging its own beta — relative weakness despite market direction.',
  '[
    {"key": "mean", "name": "mean ({{pool}})", "description": "precomputed cross-member mean close (rebased) — the industry factor line"},
    {"key": "plus1sd", "name": "+1σ", "description": "upper +1σ edge of the member dispersion"},
    {"key": "minus1sd", "name": "-1σ", "description": "lower −1σ edge of the member dispersion"}
  ]'::jsonb,
  '[
    {"key": "dynamicNames", "text": "Member lines and benchmark lines are dynamic series names (index/benchmark names); one color per industry in multi-industry mode."},
    {"key": "subPlots", "text": "The PE sub-plot is unitless mean PE per pool; the amount sub-plot is total trading amount in 亿元 (yuan / 1e8)."}
  ]'::jsonb,
  0
),
(
  'ui.industry-sentiments.market-trend',
  NULL,
  'Broad-market overview: the four A-share indices'' closes rebased to 100 × P_t/P_0 for direct relative comparison, each index''s turnover embedded as stacked bars. The ascendant rebased line flags leadership; rotation between lines = cross-market money migration; the turnover bars expose where participation pools — leadership on shrinking turnover is the weaker claim.',
  '[
    {"key": "000001", "name": "{{name}}", "description": "daily close rebased to 100 at the common start"},
    {"key": "000001-amt", "name": "{{name}} Amt", "unit": "亿", "description": "{{name}} daily trading amount — the participation behind its move"},
    {"key": "399001", "name": "{{name}}", "description": "daily close rebased to 100 at the common start"},
    {"key": "399001-amt", "name": "{{name}} Amt", "unit": "亿", "description": "{{name}} daily trading amount — the participation behind its move"},
    {"key": "399006", "name": "{{name}}", "description": "daily close rebased to 100 at the common start"},
    {"key": "399006-amt", "name": "{{name}} Amt", "unit": "亿", "description": "{{name}} daily trading amount — the participation behind its move"},
    {"key": "000680", "name": "{{name}}", "description": "daily close rebased to 100 at the common start"},
    {"key": "000680-amt", "name": "{{name}} Amt", "unit": "亿", "description": "{{name}} daily trading amount — the participation behind its move"}
  ]'::jsonb,
  '[
    {"key": "rebased", "text": "Closes are REBASED to 100 — absolute price levels are not comparable across indices."}
  ]'::jsonb,
  0
),
(
  'ui.composites.offset-correlations',
  NULL,
  'Benchmark-offset industry correlations: each industry''s MA trend has the benchmark rebased to its window-start level subtracted (common market factor removed), then every industry PAIR''s Pearson r is charted per rolling 20/60/255-day window as horizontal segments from each window date. Overall = raw r (benchmark still in); Benchmark Offset = r after removal — offset −1 = perfectly opposite ex-market, 0 = uncorrelated, +1 = co-moving. Pairs near −1 are the minority and the prize: natural market-neutral candidates — long one, hedge with the other — and the seed population of the pair-hedge universe.',
  NULL,
  '[
    {"key": "pairLines", "text": "One line per industry pair (dynamic series names); windows start every 20 trading days."},
    {"key": "offsetDetector", "text": "An industry up while the benchmark rises MORE is DOWN after the offset — that is the opposite-industry detector."}
  ]'::jsonb,
  0
),
(
  'ui.composites.pair-ratio',
  NULL,
  'Minimum-variance hedge ratio h* = −Cov(A,B)/Var(A) of the selected subject industry vs benchmark industry over trailing 20/60/255-day windows on the stride grid. h* > 0 = the pair moves opposite: buy the subject at h* units to hedge one unit of the benchmark (the hedge-grade anti-correlation regime — a minority of pairs); h* < 0 = short it. Market-offset mode strips the broad-market pct from both sides first — where the page''s opposite-moves finding becomes the buy-to-hedge answer.',
  NULL,
  '[
    {"key": "strideGrid", "text": "Values appear only on stride-grid dates; null days are skipped, never zero-filled."}
  ]'::jsonb,
  0
),
(
  'ui.perf-attr.panel',
  NULL,
  'Performance attribution for {{code}} vs its benchmark family. Per-benchmark contribution = fractional benchmark return × composition overlap (shared weight) — how much of the subject''s move each benchmark''s return explains through membership. Read the pair together: contribution earned on thin overlap = concentrated idiosyncratic edge; fat overlap with thin contribution = dead-weight membership. The expanded charts pair ETF turnover (relative liquidity / crowding) and the close trends (relative strength), optionally rebased to a common base date.',
  '[
    {"key": "contribution", "name": "Contribution", "unit": "%", "description": "benchmark fractional return × composition overlap (shared weight) — the explained part of the subject''s return"},
    {"key": "sharedWt", "name": "Shared Wt %", "unit": "%", "description": "share of the subject''s composition the benchmark overlaps — the exposure base the contribution is earned on"},
    {"key": "benchAmt", "name": "{{benchName}} ETF Amt", "unit": "亿元", "description": "benchmark index ETF turnover — the benchmark''s liquidity/crowding"},
    {"key": "subjectAmt", "name": "{{subjectName}} ETF Amt", "unit": "亿元", "description": "subject index ETF turnover — compare vs the benchmark''s for the relative-liquidity read"},
    {"key": "subjectClose", "name": "{{subjectName}}", "description": "subject close, % change vs the common base date in % mode"},
    {"key": "benchClose", "name": "{{benchName}}", "description": "benchmark close, % change vs the common base date in % mode — the relative-strength reference"}
  ]'::jsonb,
  '[
    {"key": "sqlReturns", "text": "Returns are computed on the fly in SQL (fractional, scale-invariant) — not stored in the DB."}
  ]'::jsonb,
  0
),
(
  'ui.composition-pie.industry',
  NULL,
  'Holdings composition of {{code}} by industry (stats.sec_composition, snapshot {{snapshot}}{{#quarter}} · {{quarter}}{{/quarter}}): slices are industries aggregated by weight %. A dominant slice = concentration risk to the whole position; compare against the industry''s broad-market weight to tell an overweight bet from the index default — the fund is paid for the former, not the latter.',
  NULL,
  '[
    {"key": "indexFallback", "text": "Index-fallback mode: full constituent weights of the tracking index, not fund holdings."}
  ]'::jsonb,
  0
),
(
  'ui.composition-pie.stock',
  NULL,
  'Individual stocks within industry "{{industry}}" of {{code}}''s holdings (stats.sec_composition): slice size = the stock''s weight %. A heavy single name = idiosyncratic risk to the whole position (one earnings event moves the fund); a long flat tail = the exposure is industry-beta, not stock-picking.',
  NULL, NULL, 0
);
