-- ============================================================================
--  AI prompts seed — skills / biz_guide, COMMONS: one guide per
--  commons analysis scope (the MA lens incl. its roof/floor sub scope,
--  valuation, dividends, ETF holdings, market beta, crowdedness,
--  hot-stock momentum). One of the static/ai_prompts/ seed files —
--  DDL + the shared contract: 06_ai_prompts.sql.
--
--  Every scope-bound guide carries its bound_labels (the GIN-indexed
--  force-load keyword array matched against the run's question) and
--  its optional_labels (the GIN-indexed English search vocabulary;
--  contract: 06_ai_prompts.sql).
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, bound_labels, optional_labels, ordinal) VALUES
    ('ma-spread-guide',
     'biz_guide',
     1,
     'MA spread & alignment guide',
     1,
     'ma-spread-guide: the moving average is the smoothed average '
     'holding cost of everyone who traded inside its window — the '
     'shared reference line price trades against. Read its state as '
     'trade meaning, not arithmetic: bullish alignment (多头排列, '
     'price > short > long MAs) = a confirmed, healthy trend whose '
     'pullbacks into the MA stack are the buyable zones; bearish '
     'alignment (空头排列) = rallies are sold into (distribution). '
     'The price−MA spread is EXTENSION: a wide premium invites '
     'profit-taking and mean-reversion, a deep discount is the '
     'oversold stretch snapbacks come from (sharp falls revert) — '
     'always quote it with its own-year percentile, how stretched vs '
     'the security''s own past, never an absolute %. Crosses carry '
     'the turn semantics: reclaiming a major MA from a deep discount '
     '= trend repair (the buy); losing an MA with a failed retest '
     'rally = distribution (exit timing); MA-over-MA golden/death '
     'crosses are the slow, lagging confirmation — weakest as a '
     'standalone edge. MA compression = a coiled consolidation '
     'deciding direction; a flattening slope after a run = trend '
     'exhaustion. The long windows (120/255) are the long-horizon '
     'cost basis — major support in uptrends, overhead resistance '
     'after breakdowns. Judge every reversion claim against the '
     'security''s trend regime (reversion edges live in fall/bottom '
     'states), never the MA picture alone.',
     'What the moving-average state means for trading — alignment, extension vs the security''s own history, crossing semantics and the trend regime.',
     'One guide per biz scope: the MA lens (commons/ma-spread).',
     ARRAY['均线', '移动平均', 'MA20'],
     ARRAY['moving average', 'ma alignment', 'ma spread', 'golden cross', 'death cross', 'trend regime'],
     10),
    ('roof-floor-guide',
     'biz_guide',
     1,
     'Roof & floor levels guide',
     1,
     'roof-floor-guide: the roof/floor levels are the security''s own '
     'recent structural extremes — the breakout lines traders watch, '
     'self-adaptive rather than fitted. Two flavors carry different '
     'meaning: the wick-extreme level = where the last flush or '
     'euphoria actually peaked (the panic tail); the cross-method '
     'consensus level = the smoother line the broad crowd actually '
     'watches. Breach semantics, all measured: a SHALLOW roof '
     'crossing is not a sell — shallow roof breaches drift UP (the '
     'crossed roof acts as anchoring support, not resistance); the '
     'DEEP roof breach is momentum continuation through a structural '
     'high. The DEEP breakdown below the floor is the CAPITULATION '
     'V-bottom — the deeper below the consensus floor, the harder '
     'the forward bounce. The consensus-broken / wick-intact dip '
     '(the crowd''s floor broke but the extreme-wick floor holds) is '
     'the controlled dip, the bounce-prone washout. Level '
     'reliability is the method spread: a tight agreement across '
     'methods = a solid, widely-watched level; a wide spread = a '
     'weak level. None of the roof/floor readings make sells — the '
     'levels feed buys and trend context only.',
     'The roof/floor levels'' trading meaning — wick vs consensus levels and the measured breach semantics (anchoring support, capitulation V-bottom, controlled dip).',
     'Sub scope of the MA machinery: the roof/floor level estimates.',
     ARRAY['压力', '支撑', '顶底'],
     ARRAY['roof floor levels', 'support resistance', 'donchian', 'level breach', 'consensus level'],
     11),
    ('pe-guide',
     'biz_guide',
     1,
     'PE valuation guide',
     1,
     'pe-guide: the PE is the price the market charges per unit of '
     'earnings — a valuation LEVEL that means little alone. Quote '
     'every PE with its own-history percentile: cheap/expensive is a '
     'statement about THIS security vs its OWN past, never a fixed '
     'threshold, and the percentile is the anchor that keeps the '
     'call honest. Use the persistence streaks to say how unusual '
     'the valuation regime is — a long streak at an extreme is a '
     'sustained repricing, not noise. The edge case is the rebound '
     'family''s PE-conditioned barbell: cheap-PE capitulations '
     'bounce hardest long-run; rich-PE knives take about a month to '
     'stop but the snapback is real; the middle PE zone carries no '
     'edge.',
     'How to read the PE baseline — level, its own-history percentile and the persistence streaks.',
     'The valuation lens (commons/pe).',
     ARRAY['PE', '市盈率', '估值'],
     ARRAY['pe valuation', 'pe percentile', 'valuation streaks', 'cheap expensive'],
     15),
    ('dividends-guide',
     'biz_guide',
     1,
     'Dividends guide',
     1,
     'dividends-guide: a dividend story is only as good as its '
     'delivery record — ground every yield claim in the actually '
     'delivered payouts, treat the estimates as intentions and the '
     'est-vs-delivered gap as the track record to score. Frame the '
     'yield against the security''s own history and its price level '
     '(a fat yield on a collapsing price is a distress warning, not '
     'a bargain). A dividend story without the delivery rate is an '
     'estimate story — label it as such.',
     'How to read the dividend baseline — estimated vs delivered payouts and the delivery track record.',
     'The dividends lens (commons/dividends).',
     ARRAY['分红', '股息', '派息'],
     ARRAY['dividends', 'dividend yield', 'payout estimate', 'delivery rate'],
     16),
    ('etf-holdings-guide',
     'biz_guide',
     1,
     'ETF holdings guide',
     1,
     'etf-holdings-guide: ETF holdings show where passive and '
     'structured money is parked. The trading read is the FLOW, not '
     'the list: the holdings mix, its concentration (the top names'' '
     'weight = the flow''s single-name risk) and the direction of '
     'change (accumulation vs reduction) with dates — persistent '
     'accumulation into a name or theme is real demand, reduction is '
     'supply overhang. Cross-check every "flows into X" claim '
     'against the actual holding-weight changes before asserting it.',
     'How to read the ETF holdings lens — the holdings mix, its concentration and the accumulation/reduction direction.',
     'The ETF holdings lens (commons/etf-holdings).',
     ARRAY['ETF', '重仓股', '成分股'],
     ARRAY['etf holdings', 'fund holdings', 'concentration', 'accumulation reduction'],
     17),
    ('market-beta-guide',
     'biz_guide',
     1,
     'Market beta guide',
     1,
     'market-beta-guide: an industry''s move splits into the broad '
     'market''s move (the beta part — how much of the move is just '
     'the market arriving) and the industry''s own idiosyncratic '
     'story (the part worth explaining). Read the beta''s direction '
     'and strength, whether the industry LEADS or LAGS the market at '
     'turns (a leader flipping first is the early tell), and the '
     'ETF-contribution view (which member industry actually drives '
     'the index''s move). Compare against the industry''s own '
     'trailing history before calling a beta shift a regime change — '
     'co-movement drifts; only a break from its own norm is a '
     'regime story.',
     'How to read an industry''s market-beta attribution — its co-movement with the broad market vs its own idiosyncratic drift.',
     'The market-beta lens (commons/market-beta).',
     ARRAY['beta', '贝塔', '联动'],
     ARRAY['market beta', 'industry attribution', 'co-movement', 'etf contribution'],
     18),
    ('market-crowdedness-guide',
     'biz_guide',
     1,
     'Market crowdedness guide',
     1,
     'market-crowdedness-guide: crowdedness is how ONE-SIDED the '
     'whole market''s positioning has become — participation breadth, '
     'momentum crowding and turnover aggregated into a single read. '
     'Crowding warns of reversals (everyone who wanted in is in), '
     'but it is a condition, not a timing tool: quote the level with '
     'its own-history frame (percentile / vs its MA), name WHICH '
     'side is crowded, and leave the timing call to the signal '
     'families'' measured cells, never the crowdedness level alone.',
     'How to read the market-crowdedness lens — how one-sided market positioning has become.',
     'The crowdedness lens (commons/market-crowdedness).',
     ARRAY['拥挤', '过热', '抱团'],
     ARRAY['market crowdedness', 'crowding', 'participation breadth', 'overheating'],
     19),
    ('hot-stock-momentum-guide',
     'biz_guide',
     1,
     'Hot stock momentum guide',
     1,
     'hot-stock-momentum-guide: the speculative leaders are the '
     'market''s risk-appetite gauge — where the marginal trader''s '
     'energy is parked. Report which themes and names are hot, the '
     'turnover energy behind them (a hot name on shrinking turnover '
     'is already fading) and the rotation direction (old leaders'' '
     'drain vs new leaders'' ignition). Speculative heat is a '
     'sentiment read for the whole market — tie it to the '
     'crowdedness and market-trend reads; it makes no direct index '
     'buy/sell call.',
     'How to read the hot-stock momentum lens — where speculative trading energy concentrates and how it rotates.',
     'The hot-stock lens (commons/hot-stock-momentum).',
     ARRAY['龙头', '连板', '热门'],
     ARRAY['hot stock', 'speculative leaders', 'theme rotation', 'turnover energy'],
     20)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        bound_labels = EXCLUDED.bound_labels,
        optional_labels = EXCLUDED.optional_labels,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
