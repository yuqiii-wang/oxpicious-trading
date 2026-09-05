-- ============================================================================
--  Table: analysis_forecasts.opp_pair_state
--
--  Sixth MOTIVATION (bucket-defining) table of the forecast analysis:
--  INDUSTRY OPPOSITE-PAIR trend buckets over the pair set of
--  analysis_composites.industry_corr_benchmark_offsets (pool 'all',
--  benchmark 000300). By PAIR: when ONE industry's benchmark-offset
--  trend is dropping, the forecast RESULT is the future trend of the
--  OTHER side industry.
--
--  MATH (all legs on the OFFSET space the composites analysis defines)
--    MA_X[t]  = trailing-W-row rolling mean of the industry composite
--               mean_close (stats.industry_basic_stats, pool 'all').
--    MA_M[t]  = trailing-W-row rolling mean of the benchmark's
--               (stats.index_basic_stats.close) close.
--    The W-day offset trend change of industry X ending at t, with the
--    benchmark rebased at the lookback start (k = MA_X[t-W]/MA_M[t-W],
--    the composites' window math — the adjusted trend adj = MA_X - k·MA_M
--    is identically 0 at the rebasing point), normalized by the
--    industry's own MA level, reduces to the RELATIVE MA RETURN
--      rel_X(t) = MA_X[t]/MA_X[t-W] - MA_M[t]/MA_M[t-W].
--    TRIGGER ("industry A is dropping"): rel_A(t) < 0 — A's W-day
--    MA-trend return is below the benchmark's (an industry whose trend
--    grows while the benchmark grows MORE is DROPPING after the offset).
--    FORWARD TARGET (the forecast result): the OTHER side industry B's
--    normalized offset change over [t, t+n]
--      fwd_B(t,n) = MA_B[t+n]/MA_B[t] - MA_M[t+n]/MA_M[t].
--
--  BUCKETS
--    One row per (sec_type, industry_id = the dropping industry A,
--    pair_industry_id = the forecast target B, stat_month, trend_window
--    W). Every unordered pair of the offsets table is materialized in
--    BOTH directions (A drops → B forecast; B drops → A forecast).
--    trend_window W ∈ {20, 60} (the composites' short/medium trend
--    scale; 255 is a regime filter, too slow for day-level buckets).
--    STATE buckets: every qualifying day joins — no cooldown, no
--    is_market_hyped split (industries have no hype source). The side
--    is the constant 'bottom' so the shared gate machinery reads the
--    table unchanged.
--
--  RESULT DATA
--    analysis_forecasts.forecast_results via forecast_id (1:N — one
--    forecast_id → 4 period rows next/5d/20d/60d): B's forward offset
--    change stats (ave/std/max/min, occurrence_count,
--    max_low_change_ratio) and reverse_prob at B's ADAPTIVE
--    reverse_threshold (k_n·σ of B's window forward offset changes).
--    side='bottom' → reverse_prob = P(B's change > +reverse_threshold)
--    = the pair forecast's CONFIRMATION probability (B rises when A
--    drops) — NOT a reversal probability. The signals layer reads the
--    cross-period MAX(reverse_prob) as each signal row's confidence.
--
--  The config JSONB records the bucket's mean trigger trend (mean_rel)
--  and the pair's latest offsets-table context (pair_score /
--  pair_corr / score_date — a provenance snapshot, not a trigger
--  input: the triggers/targets use only window-internal data, so no
--  look-ahead).
--
--  sec_type is the constant 'index' (industry_id codes are type='index'
--  classification members) purely so the shared month-gating / gate
--  machinery works — the universe is the offsets-table pair set, NOT
--  stats.index_identity codes.
--
--  SOURCE
--    stats.industry_basic_stats.mean_close               (pool 'all')
--    stats.index_basic_stats.close                        (benchmark)
--    analysis_composites.industry_corr_benchmark_offsets  (pair set)
--
--  POPULATION
--    python -m analyze.analysis_forecasts
--      incremental: missing stat_months + refresh of the last
--      REFRESH_MONTHS; --force: delete + full recompute.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.opp_pair_state (
    sec_type          TEXT    NOT NULL,  -- constant 'index' (gate-machinery convention)
    industry_id       TEXT    NOT NULL,  -- the DROPPING industry (trigger side of the pair)
    pair_industry_id  TEXT    NOT NULL,  -- the OTHER side — whose future trend the forecast_results rows describe
    stat_month        DATE    NOT NULL,  -- completed month-end; bucket over trailing 5y window (stat_month - 5y, stat_month]
    trend_window      INTEGER NOT NULL,  -- W of the MA curves (20 | 60); PK member

    -- Constant reversal side: 'bottom' — reverse_prob = P(the other side
    -- industry's forward offset change > +reverse_threshold), the pair
    -- forecast's CONFIRMATION probability.
    side              TEXT    NOT NULL,

    -- Recorded build parameters (NOT PK — provenance; a rebuild with
    -- different values requires --force).
    benchmark_code    TEXT    NOT NULL DEFAULT '000300',
    pool_size         TEXT    NOT NULL DEFAULT 'all',

    forecast_id       BIGINT  NOT NULL,  -- 1:N link → forecast_results (4 period rows)

    CONSTRAINT pk_opp_pair_state PRIMARY KEY
        (sec_type, industry_id, pair_industry_id, stat_month, trend_window)
) PARTITION BY HASH (industry_id);

SELECT public.create_hash_partitions('analysis_forecasts', 'opp_pair_state', 16);

CREATE INDEX IF NOT EXISTS idx_opp_pair_state_forecast_id
    ON analysis_forecasts.opp_pair_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.opp_pair_state IS 'Industry opposite-pair trend buckets (motivation): per (sec_type=''index'', industry_id = the DROPPING industry, pair_industry_id = the forecast target, stat_month, trend_window) — the trailing-5y-window days where industry A''s W-day benchmark-offset MA trend is dropping (rel_A(t) = MA_A[t]/MA_A[t-W] - MA_M[t]/MA_M[t-W] < 0, the composites'' offset math normalized to a relative MA return), with the OTHER side industry B''s forward offset change (MA_B[t+n]/MA_B[t] - MA_M[t+n]/MA_M[t]) as the forecast result in analysis_forecasts.forecast_results via forecast_id. Every unordered pair of analysis_composites.industry_corr_benchmark_offsets (pool ''all'', benchmark 000300), both directions; state buckets (no cooldown, no hype split); side constant ''bottom'' so reverse_prob = P(B rises beyond B''s adaptive reverse_threshold) — the pair forecast''s CONFIRMATION probability. Sources: stats.industry_basic_stats.mean_close + stats.index_basic_stats.close + analysis_composites.industry_corr_benchmark_offsets. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.sec_type IS 'Constant ''index'' — the shared month-gating / confirmation-gate machinery keys on sec_type; industry_id codes are type=''index'' classification members. The universe is the offsets-table pair set, NOT stats.index_identity codes.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.industry_id IS 'The DROPPING industry (the trigger): its W-day benchmark-offset MA trend change rel_A(t) < 0 on every bucket day (A''s W-day MA-trend return below the benchmark''s — dropping after the offset). Lexicographically paired with pair_industry_id; both directions of every unordered pair are materialized.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.pair_industry_id IS 'The OTHER side of the pair — the forecast TARGET: the linked forecast_results rows describe THIS industry''s forward offset trend changes. The confirmation-gate calibration (analysis_signals.gate) groups by this column, and signal rows are emitted on it.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.stat_month IS 'Completed month-end date. The bucket is computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the union industry calendar.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.trend_window IS 'Trend window W (trading-day rows) of the MA curves the relative MA returns are computed on: 20 | 60. PK member.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.side IS 'Constant ''bottom'': reverse_prob = P(the other side industry''s forward offset change > +reverse_threshold) — the pair forecast''s CONFIRMATION probability (B rises when A drops), at B''s adaptive reverse_threshold (k_n·σ of B''s window forward offset changes). Mirrors the mov_* side semantics so analysis_signals.gate consumes the table unchanged (bottom → action buy on the target).';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.benchmark_code IS 'Recorded build parameter: the offset benchmark index code (default 000300 = CSI300) whose MA curve is subtracted (level-rebased) in every relative MA return.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.pool_size IS 'Recorded build parameter: the stats.industry_basic_stats pool slice of the composite closes AND the offsets-table slice the pair set is read from (default ''all'').';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.forecast_id IS '1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results (indexed; allocated by the writer, shared across all 4 periods).';
