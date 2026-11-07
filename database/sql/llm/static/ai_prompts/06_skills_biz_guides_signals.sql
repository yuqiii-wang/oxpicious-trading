-- ============================================================================
--  AI prompts seed — skills / biz_guide, SIGNALS: the signal-reading
--  guides (the families' shared evidence contract, the roof & floor
--  family AS signals, the hedge-pairs portfolio). One of the
--  static/ai_prompts/ seed files — DDL + the shared contract:
--  06_ai_prompts.sql.
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
    ('rf-break-signal-guide',
     'biz_guide',
     1,
     'Roof & floor signal cells guide',
     1,
     'rf-break-signal-guide: rf_break is the roof/floor breakout '
     'family of analysis_signals RULE CELLS — the turtle breakout on '
     'the security''s own structural range extremes (the roof/floor '
     'levels guide''s machinery). The family is ALL-BUY, each leg '
     'measured: the DEEP breach past the roof = momentum '
     'continuation through a structural high (depth-gated past a '
     'self-adaptive bar — shallow roof crossings are NOT sells, they '
     'drift UP: the crossed roof is anchoring support, not '
     'resistance); the DEEP breakdown below the floor = the '
     'CAPITULATION V-bottom (the deeper below the floor, the harder '
     'the forward bounce); the consensus-broken / wick-intact dip '
     '(the crowd''s floor broke but the extreme-wick floor holds) = '
     'the controlled-dip buy. There is NO roof or floor SELL — every '
     'sell variant measured negative. Judge each cell by its '
     'measured evidence (sample, forward move, hit rate) and its '
     'depth beyond the bar; sells of ANY family stay exit timing, '
     'never shorts.',
     'How to read the rf_break signal family''s rule cells — the depth-gated breakout buys, the all-buy side rule and the evidence columns.',
     'Roof & floor AS SIGNALS — the rf_break family, distinct from the level guide.',
     ARRAY['rf_break', '突破', '箱体'],
     ARRAY['rf break', 'breakout signal', 'roof floor breakout', 'rule cells', 'turtle breakout'],
     12),
    ('signals-families-guide',
     'biz_guide',
     1,
     'Signal families guide',
     1,
     'signals-families-guide: the analysis_signals families are '
     'measured trade meanings, not raw indicator arithmetic — read '
     'every cell as what it claims about the forward move: mov_rsi '
     'the RSI capitulation/exhaustion tails (sharp falls revert — the '
     'bottom tails buy; the top tails are exit timing), boll_std the '
     'statistical stretch beyond the ±kσ band, ma_cross the '
     'price-vs-MA trend-repair reclaims plus the market-level '
     'breadth thrust / 9:1 washout days (NOT a golden-cross family), '
     'momentum the cumulative crash/run-up log-return tails, macd '
     'the classic momentum turn, rebound the drop→rebound→resolution '
     'trio (trough-break buy, failed-reclaim exit, full-reclaim '
     'continuation), options_wall the VRP-gated OI-wall breach '
     'reversion, options_vix buy-the-panic (the corr×vol-spike '
     'interaction is the signal — either axis alone is baseline). '
     'The equity asymmetry rules EVERY family: sharp falls revert '
     '(buys), sharp rises continue (sells are EXIT TIMING, never '
     'shorts). Judge a cell only on its own measured evidence — '
     'sample size, the measured forward move, the hit rate; '
     'confidence scales with breach depth and thins with sample '
     'starvation (a rare-but-real extreme trades at reduced '
     'confidence; a measured-out cell emits nothing). Read a cell '
     'as: action + side, what condition it marks, its trend/regime '
     'population and its evidence.',
     'How to read the standalone signal families'' rule cells — sides, adaptive bars, trend/regime splits and the measured evidence contract.',
     'The signals scope''s shared reading contract (nine families).',
     ARRAY['信号', 'RSI', 'MACD'],
     ARRAY['signal families', 'rule cells', 'rsi tails', 'macd crossover', 'bollinger band', 'rebound'],
     13),
    ('hedge-pairs-guide',
     'biz_guide',
     1,
     'Hedge pairs portfolio guide',
     1,
     'hedge-pairs-guide: the portfolio family buys the MARKET-WIDE '
     'capitulation wash THROUGH a negatively-correlated industry '
     'pair — the hedge is the vehicle, not a market call. The trio '
     'wash fires when BOTH offset legs AND the broad-market curve '
     'fall into their own RSI capitulation tails the SAME day: the '
     'whole complex washes out together, and the pair''s negative '
     'covariance absorbs the drawdown while the wash is held. The '
     'spread rule reads the pair''s OWN long-short spread at its RSI '
     'bottom/top tails. Read a trigger as: the pair''s curves, each '
     'witness''s RSI against its own adaptive bar, and the benefit '
     'audit — the pair value''s drawdown vs the mean leg''s (the '
     'hedge value is measured, never assumed).',
     'How to read the hedge-pairs portfolio signal — the trio wash (two offset legs + the broad market in RSI capitulation together).',
     'The portfolio lens (signals/hedge-pairs).',
     ARRAY['对冲', '配对', 'hedge'],
     ARRAY['hedge pairs', 'pair portfolio', 'rsi wash', 'long short spread', 'benefit audit'],
     25)
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
