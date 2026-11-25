-- The signal-amplification tier's news source frame (the 'news'
-- parent type, 2026-11-22): one row per trading day with the
-- daily news-text MARKET state (text.news_text_daily, scope_kind 0
-- market_all — the market-scope rows carry the full metric set). The
-- panel is MARKET-level (keyed on the SSE Composite's calendar), so
-- the state is identical context for every code: NewsAnatomy LEFT
-- joins it per DATE onto the ohlcv anatomy frame (the ma_cross
-- breadth convention) and each code's amplifiers measure against its
-- own anatomy. Dates absent from the panel (a day the panel has no
-- market row for) keep NULL state columns — no news amplifier can
-- fire there (the engine's masks are NaN-safe).
-- serves: analysis.analysis_signals.signal_amplification (NewsAnatomy)
-- args:
--   news_table: fragment — text.news_text_daily (aliased d)
SELECT extract(epoch from d.date)::float8 AS date,
       d.sent_z::float8,
       d.lex_tone_z::float8,
       d.flow_af::float8,
       d.flow_neg::float8,
       d.disp_z::float8,
       d.n_scored::float8
FROM {{news_table}} d
WHERE d.scope_kind = 0
  AND d.date IS NOT NULL
ORDER BY d.date ASC
