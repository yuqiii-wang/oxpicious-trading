-- ============================================================================
--  Table: analysis_forecasts.mov_pairs_ema
--
--  EMA sibling of analysis_forecasts.mov_pairs (08_mov_pairs.sql — the
--  MA-pair cross family on analysis.mov_ave_spreads_detail).
--  Identical bucket machinery, built instead on the EXISTING relative
--  EMA-spread columns of analysis.mov_ave_spreads_detail_ema — the
--  parent mov_ave_spread analysis's own EMA spread definitions (no new
--  EMA computation). TWO fast legs per slow window (the fast_leg
--  column):
--
--    fast_leg='ema6'  — ema6_vs_ema{W}  = (ema6  - ema_{W}) / ema_{W}
--    fast_leg='price' — price_vs_ema{W} = (price - ema_{W}) / ema_{W}
--
--  (the close-price leg merges the former separate mov_pairs_price_ema
--  family into this table — same cross machinery, same bucket keys
--  plus fast_leg.)
--
--  A day joins a bucket when the pair's spread changes sign that day:
--
--    side=top    — CROSS UP   (golden cross): spread[t] >  0 and
--                  spread[t-1] <= 0 (the fast leg rises through the
--                  slow EMA_{W} — the pair turns bullish)
--    side=bottom — CROSS DOWN (death  cross): spread[t] <  0 and
--                  spread[t-1] >= 0 (the fast leg falls through the
--                  slow EMA_{W} — the pair turns bearish)
--
--  (the [t-1] leg is the code's PREVIOUS trading-grid row; NULL
--  spreads — either leg still warming up — never trigger). Triggers are
--  ONE-DAY signals (the mov_pairs convention): a cross day's
--  predecessor sits on the other side of zero, so consecutive cross
--  days are mutually exclusive — every cross day is its own forecast
--  signal (streak_signal_days = 1; the legacy fixed-5-day cooldown was
--  removed 2026-09, and no streak-merge pass is needed).
--  pair_window ∈ {60, 120, 255}
--  (the slow leg; the 6/20 windows exist in the source but are not
--  built).
--
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_mov_pairs_ema_forecast_id). code is the ONLY identity column
--  stored here (the partition key); sec_type and stat_month live ONLY
--  in analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
--
--  Full-window gate + hype split + forecast_id link: identical to
--  mov_rsi (see 02_mov_rsi_mov_std.sql). Results (forward
--  changes / reversal probabilities) live in
--  analysis_forecasts.forecast_results via forecast_id (1:N — one
--  forecast_id → 4 period rows: next/5d/20d/mixed).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_pairs_ema (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 4 forecast_results period rows; id-only joins/searches use idx_mov_pairs_ema_forecast_id
    fast_leg        TEXT         NOT NULL,  -- fast leg of the pair: 'ema6' (ema6_vs_ema{W}) | 'price' (price_vs_ema{W} — the close price)
    pair_window     INTEGER      NOT NULL,  -- slow EMA leg of the pair (trading days): 60/120/255
    side            TEXT         NOT NULL,  -- 'top' (cross up / golden cross) | 'bottom' (cross down / death cross)

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    -- motivation cols
    regime_state    TEXT         NOT NULL,  -- the bucket's market-regime split (stats.market_regimes day label of its bucket days)

    CONSTRAINT ck_mov_pairs_ema_regime
        CHECK (regime_state IN ('calm', 'hot', 'panic', 'quiet')),

    CONSTRAINT pk_mov_pairs_ema PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_pairs_ema', 16);

CREATE INDEX IF NOT EXISTS idx_mov_pairs_ema_forecast_id
    ON analysis_forecasts.mov_pairs_ema (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_pairs_ema IS 'EMA-pair cross (golden/death cross) bucket definitions (motivation) — the EMA sibling of mov_pairs: one row per forecast_id — the days of one security-month within the trailing 5-year window ending at the bucket''s stat_month where the stored relative-EMA spread of analysis.mov_ave_spreads_detail_ema changes sign: fast_leg=''ema6'' reads ema6_vs_ema{pair_window} = (ema6 - ema_{W}) / ema_{W}, fast_leg=''price'' reads price_vs_ema{pair_window} = (price - ema_{W}) / ema_{W} (the close-price leg, merged from the former separate mov_pairs_price_ema family). side=top a CROSS UP / golden cross (spread turns > 0 from <= 0, the fast leg rises through the slow EMA), side=bottom a CROSS DOWN / death cross (spread turns < 0 from >= 0), one-day signals (2026-09: the legacy fixed-5-day cooldown was removed; a cross day''s predecessor sits on the other side of zero so consecutive cross days are mutually exclusive — every cross day is its own forecast signal with streak_signal_days = 1). Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. NO new EMA computation — the buckets read the parent mov_ave_spread analysis''s existing EMA spread columns. Source: analysis.mov_ave_spreads_detail_ema (ema6_vs_ema{W} / price_vs_ema{W}, W ∈ 60/120/255).';
COMMENT ON COLUMN analysis_forecasts.mov_pairs_ema.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs_ema.fast_leg IS 'Fast leg of the pair: ''ema6'' — the cross is read off ema6_vs_ema{pair_window}; ''price'' — the close price, read off price_vs_ema{pair_window}.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs_ema.pair_window IS 'Slow EMA leg of the pair (trading days): 60 / 120 / 255. The cross is read off the fast_leg''s spread column against ema{pair_window}.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs_ema.side IS 'Bucket side: top = cross UP / golden cross (spread[t] > 0 and spread[t-1] <= 0 — the pair turns bullish; reversals are changes below the bucket''s FIXED 1% threshold (0.01 — the period-end n-day close vs ±1%); bottom = cross DOWN / death cross (spread turns < 0 from >= 0 — the pair turns bearish; reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_pairs_ema.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s trigger days — every trigger day joins exactly one regime, so each (config, regime) pair is its own bucket. Replaces the retired is_market_hyped boolean (stats.mov_ave_market_hypes episode overlap); hot is the closest successor of the old TRUE split.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs_ema.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';

-- ----------------------------------------------------------------------------
--  Data-quality gate: the mov_pairs_ema vocabularies (shared helpers, see
--  01_forecast_results.sql / 00_partition_utils.sql). NOT VALID first,
--  validated once by the schema-wide sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_forecasts.mov_pairs_ema',
    'chk_mov_pairs_ema_side',
    $chk$side IN ('top', 'bottom')$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.mov_pairs_ema',
    'chk_mov_pairs_ema_fast_leg',
    $chk$fast_leg IN ('ema6', 'price')$chk$);
SELECT public.validate_pending_checks('analysis_forecasts');
