-- ============================================================================
--  static.docs seed — analysis-page chart specs: PE, ETF holdings,
--  market beta, composites, perf-attr, composition pies.
--  (Prefix re-sync per file: each DELETE below scopes its rows.)
-- ============================================================================

DELETE FROM static.docs WHERE doc_id LIKE 'ui.pe.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.etf-holdings.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.market-beta.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.composites.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.perf-attr.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.composition-pie.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.market-beta.page';
-- legacy prefix cleanup (page renamed industry-sentiments → market-beta 2026-10-29)
DELETE FROM static.docs WHERE doc_id LIKE 'ui.industry-sentiments.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.etf-holdings.page';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.dividends.%';

INSERT INTO static.docs (doc_id, title, intro, series, notes, ordinal) VALUES
(
  'ui.market-beta.page',
  NULL,
  'Market beta for {{label}}: each member index''s daily close rebased to 100 at the visible window''s start, merged across the picked industries, with the server-precomputed mean ±1σ band (anchored at history start — aligned with the lines only at full range). Sub-plots: the cross-sectional mean PE and mean trading amount (亿元). Only indices with composition data appear; broad-market indices sit under the FIN sector.',
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
  'ui.market-beta.benchmark-price',
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
  'ui.market-beta.attribution',
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
  'ui.market-beta.plot',
  NULL,
  'Market beta for {{title}}: member closes rebased to 100 × P_t/P_0 on a shared date axis (pool scoped to small/mid/large members), broad-market benchmarks overlaid, with mean ±1σ dispersion bands and sub-plots for the industry''s mean PE and total trading amount (亿元). A tight band = a consensus tape (the industry factor dominates); wide σ = member dispersion (stock selection over beta); a benchmark outrunning the members = the industry lagging its own beta — relative weakness despite market direction.',
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
  'ui.market-beta.market-trend',
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
  'ui.composites.ai-market-qa.feed',
  NULL,
  'The AI 市场情绪问答 feed (AI Market Sentiments QA), scoped to {{scope}} ({{mode}} mode{{#keyword}} · keyword “{{keyword}}”{{/keyword}}{{#date}} · day {{date}} ±120d{{/date}}). The cards are the LLM Q&A knowledge base (text.llm_qa, written daily by llm_agents): one ask-and-answer per post — the question, the answer, and its per-ref citations; click a card to expand. The metrics table audits the corpus through the SAME keyword vocabulary as the news side: per day, how many TIMES each market-description keyword family occurs across the questions+answers (下跌, 大跌… vs 上涨, 大涨…; 放量… vs 缩量…; 利空… vs 利好… — every hit counts, negated 并未下跌 never does), each with its 10y rarity rank. What the AI layer is SAYING — read it against the news audit (the Market Sentiments News page) for tone divergence: AI echoing news = the corpus explaining the tape; AI spiking while news is flat = amplification beyond the sources, discount that framing.',
  NULL,
  '[
    {"key": "scope", "text": "The ask rides the CURRENT scope — the classification pick (Industry mode) or the latest month''s top-N HYPE + DRAIN industries plus the broad-market daily QA (Market mode); the keyword-audit metrics resolve at market level."},
    {"key": "dayWindow", "text": "A picked day windows the feed ±120 calendar days around itself (not a single-day filter) and seeds the as-of date — the metrics table re-resolves at that day."}
  ]'::jsonb,
  0
),
(
  'ui.composites.market-sentiment-news.feed',
  NULL,
  'The 市场情绪新闻 feed (Market Sentiments News), scoped to {{scope}} ({{mode}} mode{{#keyword}} · keyword “{{keyword}}”{{/keyword}}{{#date}} · day {{date}} ±120d{{/date}}). The cards are the raw news corpus (text.news): zhihu market recaps, gov.cn policy releases, PBoC operations — title, snippet, and the full article with comments on click. The metrics table is the corpus AUDIT behind the chatter: per day, how many TIMES each market-description keyword family''s keywords OCCUR (下跌, 大跌… vs 上涨, 大涨…; 放量… vs 缩量…; 利空… vs 利好… — every hit counts, 大涨 twice in one article = 2; negated 并未下跌 never does), each with its 10y rarity rank (p95+ = a chatter burst). Read the PAIRS, not single gauges: 放量+上涨 = conviction, 缩量+下跌 = grind-lower risk; tone prints AFTER price — the audit is a confirmation/positioning layer, and this feed shows the actual posts behind any spike.',
  NULL,
  '[
    {"key": "scope", "text": "The ask rides the CURRENT scope — the classification pick (Industry mode) or the latest month''s top-N HYPE + DRAIN industries plus the broad-market daily QA (Market mode); the keyword audit itself resolves at market level."},
    {"key": "dayWindow", "text": "A picked day windows the feed ±120 calendar days around itself (not a single-day filter) and seeds the as-of date — the metrics table re-resolves at that day."}
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
),
(
  'ui.market-beta.corr',
  NULL,
  'Windowed pairwise correlation of the selected industries'' mean curves: for every industry pair, the Pearson r of the two industries'' MA-W mean_close curves over the W-trading-day window starting on each grid date. +1 = the pair''s trends lock-step (one factor, one money); near 0 = independent (pair-diversification material); deep negative = structurally opposite — the natural hedge candidate (long one, hedge with the other). Negative collapsing toward 0 = the hedge decaying; a fresh slide into negative = a new pair-hedge forming.',
  NULL,
  '[
    {"key": "dynamicNames", "text": "Series names are industry pairs (A ↔ B); the tooltip shows all three windows (20/60/255d) at the hovered window start — the selected one highlighted."},
    {"key": "strideGrid", "text": "Values appear only on the stride grid (a window starts every 20 trading days); dates between are absent, never zero-filled."}
  ]'::jsonb,
  0
),
(
  'ui.market-beta.etf-price',
  NULL,
  'Daily close of every ETF tracking a member index of the selected industries, CASCADINGLY rebased: each ETF indexes 100 × P_t/P_own-first-close, and a later-listed ETF starts at the already-active mean on its first date so it blends in instead of jumping to 100. Fanning curves = dispersion among the industry''s tracking products (liquidity/style splits); one curve breaking above the pack = the flows'' chosen vehicle. Trading Amt mode overlays each ETF''s turnover bars plus the industry-total {{ma}} line — turnover confirming price is conviction, price crawling up on dying turnover is drift.',
  NULL,
  '[
    {"key": "dynamicNames", "text": "Series names are the tracking ETF names; turnover bars are \"<ETF> (amt)\" on the right axis, the industry-total trading-amount MA line is \"<industry> {{ma}}\"."},
    {"key": "cascadingRebase", "text": "Rebase is CASCADING — curves do NOT share a common start; compare slopes and relative levels after each curve''s own first date, never absolute heights across listing dates."}
  ]'::jsonb,
  0
),
(
  'ui.market-beta.etf-contribution',
  NULL,
  'Per-ETF contribution inside {{industry}} as of {{date}}: each bar pair = one tracking ETF — left axis its trading amount (亿 CNY, colored by the day''s return direction), right axis its % share of the industry''s total ETF turnover. Dominant amount + swelling share = the money''s chosen vehicle for the industry (the liquidity anchor); a thin ETF''s share spiking = hot money squeezing through a product that cannot absorb it (slippage risk). Read against the industry total and its MA5 in the tooltip: participation expanding = the industry move is funded, share flattening into one name = crowding.',
  '[
    {"key": "tradingAmt", "name": "Trading Amt", "unit": "亿 CNY", "description": "the ETF''s daily trading amount — its capital-flow weight inside the industry"},
    {"key": "sharePct", "name": "Share %", "unit": "%", "description": "the ETF''s share of the industry''s total ETF trading amount"}
  ]'::jsonb,
  NULL,
  0
),
(
  'ui.market-beta.index-allocation',
  NULL,
  'Close curves of every selected member index, rebased to 100 × P_t/P_window-start for direct relative comparison (actual closes in the tooltip). The ascendant line flags the pick''s leading index; a tight cluster = the group trades as one factor; a fanning pack = internal rotation — the relative-strength leader is where the industry''s beta concentrates. The panels below attribute each index''s fluctuation to its benchmark family.',
  NULL,
  '[
    {"key": "dynamicNames", "text": "Series names are the member index names (prefixed \"[industry]\" when several industries are selected); each is rebased to 100 at the visible window''s start."},
    {"key": "fullSet", "text": "The plot shows the FULL selection (matching the nav chips) — not narrowed to indices that have allocation data; the attribution panels below are."}
  ]'::jsonb,
  0
),
(
  'ui.market-beta.hypes-drains',
  NULL,
  'Seasonal HYPE/DRAIN ranking against {{benchmark}}: each month the industries are ranked by trailing {{period}}-day attribution contribution — HYPE lifted the benchmark, DRAIN dragged it. The curves are daily (rebased to 100 over the trailing window) but the CAST freezes per month: ● active (this month''s top/bottom {{maxRank}}), ○ fading (a past month''s rank, still on the same side of the benchmark), ✕ hidden (crossed the benchmark or never ranked). A HYPE name''s curve rolling over = the hype exhausting (crowd exit watch); a DRAIN name basing = the drain drying (capitulation-buy watch).{{#amt}} Ranked by contribution × shared turnover — absolute yuan impact.{{/amt}}{{^amt}} Ranked by raw contribution — every industry weighted equally.{{/amt}}',
  NULL,
  '[
    {"key": "seasonalCast", "text": "Which industries appear is frozen per MONTH (seasonal rankings); the ranking table below the chart follows the clicked date''s season."},
    {"key": "rebased", "text": "Curves are REBASED to 100 over the trailing {{period}}d — absolute levels are not comparable; slopes and benchmark crossovers are the read."}
  ]'::jsonb,
  0
);
