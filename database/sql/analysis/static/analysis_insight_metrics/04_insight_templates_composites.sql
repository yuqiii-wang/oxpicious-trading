-- ============================================================================
--  analysis_insight_metrics.insight_templates seed — 'Composites' tab (composites).
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

DELETE FROM analysis_insight_metrics.insight_templates WHERE tab = 'composites';

INSERT INTO analysis_insight_metrics.insight_templates
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

    -- market-sentiment-news · Market Sentiments News (市场情绪新闻) [scope=market]
    --
    -- NOT numerics: this page's ask audits the news CORPUS through the
    -- seeded keyword vocabulary (static.keyword_maps) — per day, how many
    -- TIMES each market-description keyword family's keywords OCCUR in
    -- text.news (下跌, 大跌, 上涨, 大涨, 放量, 缩量, 利空, 利好, … —
    -- every hit counts, 大涨 twice in one article = 2; negated hits
    -- 并未下跌 never count), resolved from the text.news_keyword_audit
    -- rollup's mentions column. The pairs are the read: 下跌 vs 上涨
    -- (tone split), 放量 vs 缩量 (participation), 利空 vs 利好 (headline
    -- tone); every metric carries its 10y rarity rank — a chatter burst
    -- into stretched price is positioning, not information. (Split
    -- 2026-11-01 from the former combined 'ai-market-sentiments' page —
    -- the AI Q&A side lives on 'ai-market-qa'; corrected from article
    -- breadth to keyword OCCURRENCES the same day.)
    INSERT INTO analysis_insight_metrics.insight_templates
    (template_id, tab, page, metric, label, unit, description, spec, ordinal)
    SELECT * FROM (VALUES
    ('analysis.composites.market-sentiment-news.news_px_dn_occurrences', 'composites', 'market-sentiment-news', 'news_px_dn_occurrences', 'Down-talk occurrences', '', 'Total occurrences of bearish price-talk keywords (下跌, 下降, 回落, 大跌, … — static.keyword_maps kw_market px_dn) across the day''s news corpus (text.news: zhihu recaps, gov policy, PBoC ops). Every hit counts — 大跌 twice in one article adds 2 — and negated hits (并未下跌) never do. The fear gauge: an occurrence burst into stretched-low price = capitulation-grade crowd (fade risk, rebound watch); >0.9 pct rank = fear crowding, contrarian attention. Read against up-talk and volume chatter: 放量下跌 is pressure releasing, 缩量下跌 grinds lower.', '{"scope": "market", "page_title": "Market Sentiments News (市场情绪新闻)", "source": {"kind": "table", "table": "text.news_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 1}}}'::jsonb, 0),
    ('analysis.composites.market-sentiment-news.news_px_up_occurrences', 'composites', 'market-sentiment-news', 'news_px_up_occurrences', 'Up-talk occurrences', '', 'Total occurrences of bullish price-talk keywords (上涨, 上升, 走强, 大涨, … — kw_market px_up): the greed/euphoria side of the corpus tone. A >0.9 pct-rank occurrence burst into stretched-high price = crowded chase (sell-strength discipline); up-talk fading while down-talk stays flat = apathy, not health.', '{"scope": "market", "page_title": "Market Sentiments News (市场情绪新闻)", "source": {"kind": "table", "table": "text.news_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 0}}}'::jsonb, 1),
    ('analysis.composites.market-sentiment-news.news_vol_up_occurrences', 'composites', 'market-sentiment-news', 'news_vol_up_occurrences', 'Vol-expansion occurrences', '', 'Occurrences of participation-EXPANSION keywords (放量, 增量资金, 天量, … — kw_market vol_up; house semantics: 成交额 share above its own MA, never absolute yuan). The engagement gauge: volume chatter alongside direction talk marks conviction (放量上涨 confirms, 放量下跌 releases); a vol_up burst on quiet price = stealth-accumulation watch.', '{"scope": "market", "page_title": "Market Sentiments News (市场情绪新闻)", "source": {"kind": "table", "table": "text.news_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 10}}}'::jsonb, 2),
    ('analysis.composites.market-sentiment-news.news_vol_dn_occurrences', 'composites', 'market-sentiment-news', 'news_vol_dn_occurrences', 'Vol-contraction occurrences', '', 'Occurrences of participation-CONTRACTION keywords (缩量, 地量, 成交萎缩, … — kw_market vol_dn): willingness fading. 缩量 chatter beside 下跌 = no-panic grind-lower risk (阴跌); beside 反弹 = a weak, fade-prone rebound. A vol_dn floor with vol-expansion chatter rising is the classic repair signal.', '{"scope": "market", "page_title": "Market Sentiments News (市场情绪新闻)", "source": {"kind": "table", "table": "text.news_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 11}}}'::jsonb, 3),
    ('analysis.composites.market-sentiment-news.news_bearish_occurrences', 'composites', 'market-sentiment-news', 'news_bearish_occurrences', 'Bearish-tone occurrences', '', 'Occurrences of negative-headline keywords (利空, 看空, 熊市, 做空, … — kw_market bearish): policy/news headwind intensity. Rank spikes cluster at drawdown accelerations — the tone prints AFTER price, so treat it as confirmation, not a trigger; pair with the broadmarket policy themes (kw_theme fiscal/monetary) to see whether the headwind is policy-side.', '{"scope": "market", "page_title": "Market Sentiments News (市场情绪新闻)", "source": {"kind": "table", "table": "text.news_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 14}}}'::jsonb, 4),
    ('analysis.composites.market-sentiment-news.news_bullish_occurrences', 'composites', 'market-sentiment-news', 'news_bullish_occurrences', 'Bullish-tone occurrences', '', 'Occurrences of positive-headline keywords (利好, 看多, 牛市, 做多, … — kw_market bullish): policy/news tailwind intensity. Sustained >0.75 rank with bearish tone falling = regime support; a bullish burst while price-talk occurrences stay weak = headline hope, not participation — discount it.', '{"scope": "market", "page_title": "Market Sentiments News (市场情绪新闻)", "source": {"kind": "table", "table": "text.news_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 13}}}'::jsonb, 5)
    ) AS v(template_id, tab, page, metric, label, unit, description, spec, ordinal);

    -- ai-market-qa · AI Market Sentiments QA (AI 市场情绪问答) [scope=market]
    --
    -- The SAME keyword audit over the AI Q&A corpus: per day, how many
    -- TIMES each market-description keyword family's keywords OCCUR across
    -- the day's ACTIVE llm_qa questions+answers (llm.llm_qa_keyword_audit
    -- mentions — builds.text extracts with the news audit's vocabulary
    -- and negation rules). The read is the DIVERGENCE against the news
    -- audit (market-sentiment-news): AI tone echoing news tone = the
    -- corpus explaining the tape; AI down-talk spiking while news
    -- down-talk is flat = the AI layer amplifying fear beyond its
    -- sources. (2026-11-01 split of the combined page; corrected from
    -- coverage counts to the keyword audit the same day.)
    INSERT INTO analysis_insight_metrics.insight_templates
    (template_id, tab, page, metric, label, unit, description, spec, ordinal)
    SELECT * FROM (VALUES
    ('analysis.composites.ai-market-qa.qa_px_dn_occurrences', 'composites', 'ai-market-qa', 'qa_px_dn_occurrences', 'Down-talk occurrences', '', 'Total occurrences of bearish price-talk keywords (下跌, 下降, 回落, 大跌, … — kw_market px_dn) across the day''s ACTIVE AI Q&A questions+answers (llm.llm_qa through the news audit''s own vocabulary and negation rules — 并未下跌 does not count). What the AI corpus SAYS about downside; read against the news-side down-talk occurrences: AI ≈ news = the corpus explaining the tape, AI ≫ news = the AI layer amplifying fear beyond its sources (discount the answers'' tone accordingly).', '{"scope": "market", "page_title": "AI Market Sentiments QA (AI 市场情绪问答)", "source": {"kind": "table", "table": "llm.llm_qa_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 1}}}'::jsonb, 0),
    ('analysis.composites.ai-market-qa.qa_px_up_occurrences', 'composites', 'ai-market-qa', 'qa_px_up_occurrences', 'Up-talk occurrences', '', 'Occurrences of bullish price-talk keywords (上涨, 上升, 走强, 大涨, … — kw_market px_up) in the day''s AI Q&A questions+answers. AI up-talk spiking while news up-talk stays flat = the answers leaning bullishly beyond the sources; both spiking into stretched-high price = crowded conviction story, sell-strength discipline.', '{"scope": "market", "page_title": "AI Market Sentiments QA (AI 市场情绪问答)", "source": {"kind": "table", "table": "llm.llm_qa_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 0}}}'::jsonb, 1),
    ('analysis.composites.ai-market-qa.qa_vol_up_occurrences', 'composites', 'ai-market-qa', 'qa_vol_up_occurrences', 'Vol-expansion occurrences', '', 'Occurrences of participation-EXPANSION keywords (放量, 增量资金, 天量, … — kw_market vol_up) in the day''s AI Q&A. The AI answers narrating 放量 alongside direction talk = conviction reading; a QA 放量 burst on quiet price = the corpus front-running engagement that has not printed yet.', '{"scope": "market", "page_title": "AI Market Sentiments QA (AI 市场情绪问答)", "source": {"kind": "table", "table": "llm.llm_qa_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 10}}}'::jsonb, 2),
    ('analysis.composites.ai-market-qa.qa_vol_dn_occurrences', 'composites', 'ai-market-qa', 'qa_vol_dn_occurrences', 'Vol-contraction occurrences', '', 'Occurrences of participation-CONTRACTION keywords (缩量, 地量, 成交萎缩, … — kw_market vol_dn) in the day''s AI Q&A. QA 缩量 chatter beside 下跌 explanations = the answers reading a no-panic grind (阴跌); beside 反弹 = a weak, fade-prone rebound being flagged by the AI layer itself.', '{"scope": "market", "page_title": "AI Market Sentiments QA (AI 市场情绪问答)", "source": {"kind": "table", "table": "llm.llm_qa_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 11}}}'::jsonb, 3),
    ('analysis.composites.ai-market-qa.qa_bearish_occurrences', 'composites', 'ai-market-qa', 'qa_bearish_occurrences', 'Bearish-tone occurrences', '', 'Occurrences of negative-headline keywords (利空, 看空, 熊市, 做空, … — kw_market bearish) in the day''s AI Q&A. The answers'' headwind narration; a QA bearish burst while the news bearish tone is flat = the AI layer amplifying negativity — confirmation bias risk in the knowledge base, discount the answers'' framing.', '{"scope": "market", "page_title": "AI Market Sentiments QA (AI 市场情绪问答)", "source": {"kind": "table", "table": "llm.llm_qa_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 14}}}'::jsonb, 4),
    ('analysis.composites.ai-market-qa.qa_bullish_occurrences', 'composites', 'ai-market-qa', 'qa_bullish_occurrences', 'Bullish-tone occurrences', '', 'Occurrences of positive-headline keywords (利好, 看多, 牛市, 做多, … — kw_market bullish) in the day''s AI Q&A. The answers'' tailwind narration; sustained QA bullish tone with news tone falling = narrative hope outpacing the sources — the classic late-cycle divergence.', '{"scope": "market", "page_title": "AI Market Sentiments QA (AI 市场情绪问答)", "source": {"kind": "table", "table": "llm.llm_qa_keyword_audit", "value": "mentions", "date": "date", "match": {"category_type": 1, "category_code": 13}}}'::jsonb, 5)
    ) AS v(template_id, tab, page, metric, label, unit, description, spec, ordinal);

    -- news-text-states (raw layer) · the Text Sentiment Metrics metrics
    -- [scope=market] — re-keyed from the deleted 'text-metrics' page to
    -- the merged News Text States page (2026-11-22 page merge); the
    -- ck_insight_templates_id_shape contract re-keys the template_ids
    -- with the page (analysis.<tab>.<page>.<metric>).
    --
    -- The daily abnormal-sentiment layer over the news corpus
    -- (analysis.text_metrics_daily, the '' broad-market row — ALL
    -- digestible articles that day): Tetlock (2007)-style abnormal
    -- sentiment z, the Heston-Sinha abnormal news-flow z, the
    -- closed-taxonomy keyword-tone overlay and the cross-article
    -- dispersion. The LLM level itself is NEVER the signal (the jev
    -- scorer sits ~+0.6 positive over the 2020-2026 corpus) — every
    -- metric here reads DEVIATIONS from the corpus's own trailing
    -- history (60-article-day window, built by analysis.text_metrics).
    -- Same-day validation: sentiment_z co-moves ~0.45 with the index's
    -- day return while the NEXT day carries ~no drift — read these as
    -- the mood STATE (assistant info), not a next-day trigger.
    INSERT INTO analysis_insight_metrics.insight_templates
    (template_id, tab, page, metric, label, unit, description, spec, ordinal)
    SELECT * FROM (VALUES
    ('analysis.composites.news-text-states.sentiment_z', 'composites', 'news-text-states', 'sentiment_z', 'Abnormal sentiment (z)', 'σ', 'Tetlock-style abnormal news sentiment: the day''s mean LLM sentiment (jev-scored [-5,+5] over every digestible article) minus its trailing 60-article-day mean, in prior-window std units — the DEVIATION is the signal, never the level. Deep-negative z into stretched-low price = capitulation-adjacent fear print (rebound watch, fade the panic); deep-positive z into stretched-high price = euphoria crowding (sell-strength discipline). The day''s price co-moves with z (~0.45) but the next day carries ~no drift — state reading, not a trigger.', '{"scope": "market", "page_title": "News Text States (新闻文本状态)", "source": {"kind": "table", "table": "analysis.text_metrics_daily", "value": "sentiment_z", "date": "date", "match": {"industry_id": ""}}}'::jsonb, 0),
    ('analysis.composites.news-text-states.flow_z', 'composites', 'news-text-states', 'flow_z', 'Abnormal news volume (z)', 'σ', 'Abnormal news-flow z-score: the day''s digestible-article count vs its trailing 60-article-day distribution — attention surging or shrinking. Flow beats tone beyond a day (Heston-Sinha): a flow burst into stretched-high price = attention crowding (chase risk); a burst with falling prices = panic coverage (pressure releasing when the 放量下跌 occurrences confirm, grind risk when 缩量 does). Negative flow_z = quiet tape — a tone print on a silent corpus is low-conviction.', '{"scope": "market", "page_title": "News Text States (新闻文本状态)", "source": {"kind": "table", "table": "analysis.text_metrics_daily", "value": "flow_z", "date": "date", "match": {"industry_id": ""}}}'::jsonb, 1),
    ('analysis.composites.news-text-states.kw_tone', 'composites', 'news-text-states', 'kw_tone', 'Lexicon tone (bull−bear)', '', 'Deterministic lexicon tone: (bullish − bearish kw_market keyword mentions) / total, in [-1, +1] — the closed-taxonomy cross-check on the LLM sentiment (negation handled at extraction; non-directional codes excluded). LLM z and lexicon tone DISAGREEING = ambiguous news day, discount both; agreeing at an extreme = conviction print. NULL on days with no directional mentions.', '{"scope": "market", "page_title": "News Text States (新闻文本状态)", "source": {"kind": "table", "table": "analysis.text_metrics_daily", "value": "kw_tone", "date": "date", "match": {"industry_id": ""}}}'::jsonb, 2),
    ('analysis.composites.news-text-states.sentiment_std', 'composites', 'news-text-states', 'sentiment_std', 'Sentiment dispersion', '', 'Cross-article std of the day''s sentiment scores — how much the corpus''s voices disagree. Rising dispersion on big price days = the market arguing about the move (inflection-prone); clustered tone (low dispersion) with a directional z = consensus narrative (trend-friendly but crowded-exit risk on the flip).', '{"scope": "market", "page_title": "News Text States (新闻文本状态)", "source": {"kind": "table", "table": "analysis.text_metrics_daily", "value": "sentiment_std", "date": "date", "match": {"industry_id": ""}}}'::jsonb, 3),
    ('analysis.composites.news-text-states.sentiment_ma5', 'composites', 'news-text-states', 'sentiment_ma5', 'Sentiment z (5d smoothed)', 'σ', '5-day smoothing of the abnormal sentiment z — the readable mood-state curve; zero-line crosses mark sentiment-regime flips. The raw sentiment_z is the day print, this is the mood trend: a smoothed recovery from deep negative while price is still stretched-low = the corpus turning before the tape does (early-repair tell, confirm with 放量 before trusting).', '{"scope": "market", "page_title": "News Text States (新闻文本状态)", "source": {"kind": "table", "table": "analysis.text_metrics_daily", "value": "sentiment_ma5", "date": "date", "match": {"industry_id": ""}}}'::jsonb, 4)
    ) AS v(template_id, tab, page, metric, label, unit, description, spec, ordinal);

-- ============================================================================
--  Page docs (insight_page_docs) — the tab's page-level ask descriptions
--  and biz keywords. Same per-tab DELETE+INSERT re-sync contract as the
--  metrics above.
-- ============================================================================

DELETE FROM analysis_insight_metrics.insight_page_docs WHERE tab = 'composites';

INSERT INTO analysis_insight_metrics.insight_page_docs (tab, page, title, doc, keywords) VALUES
    ('composites', 'offset-correlations', 'Offset correlations (opposite-industry)',
     'Lagged (offset) correlations between opposite-direction industries: which industry''s move leads or lags another''s — the rotation clock used to time pair entries.',
     ARRAY['offset correlation', 'lagged correlation', 'opposite industry', 'lead lag', 'rotation clock', 'hedge pair', 'diversification']),
    ('composites', 'pair-hedge-ratio', 'Pair hedge ratio vs benchmark industry',
     'The rolling hedge ratio of an industry against its benchmark opposite-industry pair — how many units of the hedge leg neutralize one unit of the exposure leg, and its stability.',
     ARRAY['hedge ratio', 'pair trading', 'beta hedge', 'benchmark industry', 'rolling hedge', 'hedge stability', 'neutral exposure']),
    ('composites', 'benchmark-price', 'Benchmark price',
     'Price of the composite benchmark legs against the industry: relative strength and excess-return context — whether the industry is beating or lagging its hedge/benchmark complex.',
     ARRAY['benchmark', 'relative strength', 'excess return', 'price ratio', 'industry vs benchmark', 'outperformance', 'underperformance']),
    ('composites', 'ai-market-qa', 'AI Market Sentiments QA (AI 市场情绪问答)',
     'The AI half of the sentiments pair: the LLM Q&A knowledge base (llm.llm_qa — one ask-and-answer per industry per day, written by llm_agents), audited through the SAME seeded keyword vocabulary as the news side (static.keyword_maps over llm.llm_qa_keywords — negation-aware: 并未下跌 never counts). Each metric counts, per day, how many TIMES a market-description keyword family OCCURS across the day''s questions+answers — 下跌… vs 上涨…, 放量… vs 缩量…, 利空… vs 利好… (every hit counts, 大涨 twice = 2) — each with its 10y rarity rank. The read is the DIVERGENCE against the news audit (market-sentiment-news): AI tone echoing news tone = the corpus explaining the tape; AI tone spiking while news tone is flat = the AI layer amplifying beyond its sources — discount that framing.',
     ARRAY['AI 问答', 'llm_qa', 'keyword audit', '下跌', '上涨', '放量', '缩量', '利好', '利空', 'AI market sentiment', 'tone divergence', 'llm.llm_qa_keyword_audit']),
    ('composites', 'market-sentiment-news', 'Market Sentiments News (市场情绪新闻)',
     'The news half of the sentiments pair: what the news corpus (text.news — zhihu market recaps, gov.cn policy, PBoC operations) is SAYING, audited through the seeded keyword vocabulary (static.keyword_maps) rather than price numerics. Each metric counts, per day, how many TIMES each market-description keyword family''s keywords OCCUR — 下跌… vs 上涨…, 放量… vs 缩量…, 利空… vs 利好… (every hit counts, 大涨 twice in one article = 2; negated hits never count) — each with its 10y rarity rank. Read the pairs, not single gauges: 放量+上涨 = conviction, 缩量+下跌 = grind risk; tone prints after price, so the audit is a confirmation/positioning layer — the feed on the page shows the actual articles behind any spike.',
     ARRAY['news sentiment', 'keyword audit', '下跌', '上涨', '放量', '缩量', '利好', '利空', 'market mood', 'fear greed', 'capitulation', 'participation', 'occurrences', 'text.news_keyword_audit']),
    ('composites', 'news-text-states', 'News Text States (新闻文本状态)',
     'The merged news-text state page (the standalone Text Sentiment Metrics page folded in 2026-11-22): FIVE semantic panels over one shared time window — the price context; the validated CFSD-lexicon-blended mood state (text.news_text_daily, built by analysis.news_text_daily: the level with its shock/persistent ewm σ lines — state, not direction, contrarian at the monthly tail); the kw tone vs mood z talk cross-check; the abnormal-coverage AF risk flag with signed article counts; and the RAW LLM-only mood layer (analysis.text_metrics_daily, built by analysis.text_metrics): Tetlock abnormal-sentiment z + its ma5 smoothing, the Heston-Sinha abnormal news-flow z, the deterministic bullish/bearish keyword-tone overlay, and the cross-article dispersion. Every raw metric reads a DEVIATION from the corpus''s own trailing 60-article-day history — the raw sentiment level is never the signal (the scorer sits ~+0.6 positive), and same-day z co-moves ~0.45 with the index day-return while the next day carries ~no drift. Read the layers together: the raw z layer is the article-date mood print, the blended panel is the T+1 tradeable framing.',
     ARRAY['news text state', 'sentiment state', 'sentiment z-score', 'abnormal sentiment', '文本情绪', '情绪指标', 'news flow', 'attention', 'dispersion', 'keyword tone', 'Tetlock', 'text.news_text_daily', 'analysis.text_metrics_daily', 'news_text_daily']);
