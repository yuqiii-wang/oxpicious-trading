-- ============================================================================
--  AI prompts seed — skills / backend_tech: the BACKEND STUDY TECH
--  guides (what DB tables a study queries, and the sandbox study_kit
--  — the finished query/calculation functions every sandbox script
--  imports). One of the static/ai_prompts/ seed files — DDL + the
--  shared contract: 06_ai_prompts.sql.
--
--  Cross-cutting conventions skills (like db_query / online_search):
--  unbound ('{}' label arrays — the agent picks them, the UI lists
--  them under the backend_tech foldable).
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, ordinal) VALUES
    ('db-table-map',
     'backend_tech',
     1,
     'DB table map for studies',
     1,
     'db-table-map: the study table map, by domain. DAILY OHLCV lives '
     'in the reconstructed views — stats.v_index_daily / v_etf_margin '
     '(plus adjusted adj_* prices) / v_stock_daily / v_futures_daily — '
     'keyed (date, code), plain 6-digit codes, no index_daily table; '
     'they carry close, trading_amount (成交额 — volume means this '
     'unless the ask names 交易量 trading_shares), the PRECOMPUTED '
     'ma5/20/60/120/252 + ema12/26 (read them, never recompute) and '
     'pct_change. TECH STATES: stats.market_regimes (calm/hot/panic/'
     'quiet), analysis.trend_days (0 top / 1 bottom / 2 rise / 3 fall '
     '/ 5 unknown). SIGNALS: analysis.analysis_signals (signal_rules '
     'cells, history_signals, history_signal_performance) — '
     'side/action/trend are SMALLINT label codes decoded via '
     'static.label_dim. COMMONS: analysis.mov_ave_spreads_codes + '
     'mov_ave_spreads_detail (MA-spread gaps/derivatives/σ) + '
     'mov_ave_spreads_detail_trailing (per-gap trailing ranks, same '
     'grain + PK) + mov_ave_spreads_detail_ema + '
     'mov_ave_spreads_detail_ohlc_gaps (K-line anatomy), '
     'mov_ave_price_vs_amt (the day''s 量价 cell), mov_ave_spreads_'
     'roof_floor_* (roof/floor levels), analysis.pe (+ pe_pct, '
     'pe_pct_streaks), analysis.dividends / dividend_est / dividend_'
     'deliveries, analysis.margin_changes / margin_index_series / '
     'exchange_margin_summary, analysis.futures_ext, analysis.options_'
     '*, analysis.industry_*, analysis.market_crowdedness, '
     'analysis.mov_ave_trading_amt (the '
     'participation share vs its own MA — the 放量/缩量 gauge). TEXT '
     'CORPUS: llm.llm_qa / news_digestions — search via the '
     'text_search tool only (GIN search_tsv; raw Chinese ILIKE '
     'matches nothing). LIVE: live.live_signals. Discipline: narrow '
     'aggregates, reads anchored on the target table''s keys, ONE '
     'extra year of warmup before a studied window (index history '
     'starts 2020-01-01), and the study scope is the 10 indices '
     '(000001, 931743, 931160, 931071, 000015, 930708, 931994, '
     '931594, 000683, 000991) unless told otherwise. In the sandbox, '
     'study_kit.db.fetch_daily_bars covers the daily views, and '
     'study_kit.db.fetch_spread_stats the detail table''s per-gap '
     'trailing pct_rank / zscore / verdict columns.',
     'What DB tables a study queries — the daily OHLCV views, the precomputed stats/analysis tables and their conventions.',
     'The backend study tech map: tables, keys, code formats, the label-code decoding.',
     27),
    ('sandbox-study-kit',
     'backend_tech',
     1,
     'Sandbox study kit guide',
     1,
     'sandbox-study-kit: the docker py-sandbox ships the finished '
     'study_kit package (one public function per file) — MATCH FIRST: '
     'import and call an existing function AS-IS rather than adding a '
     'new one; write new code only for what the kit and the tables '
     'lack. study_kit.db: query(sql, params) runs any SELECT into a '
     'DataFrame over the READ-ONLY readonly_analytics role (writes '
     'fail server-side; Decimal results arrive coerced to float); '
     'fetch_daily_bars(sec_type, code, start, end, adjusted=False) '
     'returns one code''s daily bars with the canonical columns '
     '(pct_change, trading_amount, the precomputed MA/EMA set; '
     'adjusted=True swaps the ETF prices to the adj_* set); '
     'fetch_spread_stats(sec_type, code, start, end, pairs) is the '
     'TABLE-FIRST MA-spread read — the 9 price_vs_ma*/ma5_vs_ma* gaps'' '
     'value + trailing-252d pct_rank / zscore / stretched verdict '
     '(stretched_low / neutral / stretched_high) materialized on '
     'analysis.mov_ave_spreads_detail joined to '
     'analysis.mov_ave_spreads_detail_trailing; retrieve them, never '
     'recompute. fetch_amt_metrics(sec_type, code, start, end) is the '
     'TABLE-FIRST 量能 read — the amt MAs, their Bollinger σ, the '
     'market-share MAs and the signed market-share-vs-MA gaps '
     '(放量/缩量) materialized on analysis.mov_ave_trading_amt '
     '(etf/stock/industry only). study_kit.studies (the compute '
     'fallbacks for custom parameters — windows, spans, horizons, '
     'weights, thresholds are all function args): sma / ema / '
     'wilder_rsi / macd / kdj (K/D/J zones + the K/D cross; NOT '
     'materialized) / bollinger (mid/upper/lower + pct_b + '
     'bandwidth) / roc / zscore / trailing_pct_rank (own-history '
     'percentile, 0..1) / drawdown / forward_return / forward_blend '
     '(the measured dir_ave weights) / ma_alignment '
     '(bullish/bearish/mixed) / volume_contrast (the up/down-day '
     '量能对比 the tables do not store) / spread_report '
     '(the standard MA-spread opener: per window the current spread, '
     'its trailing-year percentile, z-score and a stretched/neutral '
     'verdict — skip it when fetch_spread_stats covers the ask). The '
     'session workspace is minted with the standard '
     'layout — scripts/ for your .py files, data/ for fetched or '
     'intermediate files, outputs/ for final results, figures/ for '
     'plots — write into those lanes (the standard dirs exist; other '
     'new dirs need os.makedirs first). Caps: one write 512 KB, one '
     'sandbox_read_file 1 MB, run timeout default 120s (max 600s), '
     'stdout capped — print compact summaries, write full tables to '
     'outputs/ as CSV and read them back. Prefer ONE script doing '
     'fetch + compute + print over many round trips.',
     'The finished study_kit in the sandbox — read-only queries, daily bars, the MATERIALIZED reads (fetch_spread_stats: trailing pct_rank / zscore / verdict; fetch_amt_metrics: the 量能 lane) and the standard calculations (kdj / bollinger / volume_contrast included); match-first reuse, plus the session workspace layout.',
     'The backend study tech kit: import study_kit, don''t re-derive.',
     28)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
