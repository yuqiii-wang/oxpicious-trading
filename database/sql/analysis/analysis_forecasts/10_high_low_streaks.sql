-- ============================================================================
--  Table: analysis_forecasts.high_low_streaks
--
--  Ninth MOTIVATION (bucket-defining) table of the forecast analysis:
--  MA-Spread High/Low STREAK buckets built on the EXISTING band-break
--  excursion streaks of analysis.mov_ave_high_low_pct_streaks (the
--  mov_ave_spread analysis's own streak table — NO streak recomputation:
--  the source table is read directly; run
--  ``python -m analyze.mov_ave_spread`` first so the streak table is
--  current).
--
--  Every streak period is audited at its MEAN-MID anchor day: a streak
--  with day_count = n contributes its ((n-1)//2 + 1)-th trading day of
--  the span [start_date, end_date] as the ONE trigger day (n = 8 -> the
--  4th day; n = 7 -> the 4th day; n = 1 -> the day itself — the floor
--  of the MEAN elapsed day (n-1)/2, the "mean mid elapsed day once
--  entered a streak"). The anchor is EX-POST: the streak length — hence
--  its mid — is known only after the streak closes, so the buckets
--  audit streak-period behaviour (what the mid of a completed excursion
--  looks forward to); they are NOT a live trigger.
--
--  side derivation (the streaks table has no side column): the END date
--  is an out-of-band day by construction, so the streak's side is read
--  off the UNROUNDED close on end_date vs the end date's OWN month band
--  in analysis.mov_ave_high_low_pct — side=top when close > high_val
--  (above-band excursion), side=bottom when close < low_val (the exact
--  test the streaks step used; a tie falls to the NEARER band).
--
--  band_period / pct_type mirror the source table's band axes (255/500/
--  750/1275 rows x 1/5/10 percent). band_period (NOT "period" — that
--  name is RESERVED by the shared result-row pipeline: build_result_rows
--  stamps forecast_results' period 'next'/'5d'/'20d'/'60d' onto every
--  row dict, overwriting any same-named motivation key). NO cooldown PK
--  member — each streak
--  contributes exactly ONE trigger and streaks are inherently separated
--  (a streak ends only after a 6+-day in-band gap or a side switch), so
--  trigger suppression is redundant (state-family shape, like px_vol /
--  margin_ratio).
--
--  Full-window gate + hype split + forecast_id link: identical to
--  mov_rsi / mov_pairs (see 02_mov_rsi_mov_std.sql). Results (forward
--  changes / reversal probabilities) live in
--  analysis_forecasts.forecast_results via forecast_id (1:N — one
--  forecast_id → 5 period rows: next/5d/20d/60d/mixed), measured from the
--  ANCHOR day's close on the code's own trading-day sequence. The
--  config JSONB records the bucket's streak-length context
--  (mean/min/max day_count).
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_high_low_streaks_forecast_id). code is the ONLY identity column stored
--  here (the partition key); sec_type and stat_month live ONLY in
--  analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.high_low_streaks (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_high_low_streaks_forecast_id
    band_period     INTEGER      NOT NULL,  -- band lookback of the audited streak (trading rows): 255/500/750/1275 (NOT "period" — reserved by the result-row pipeline)
    pct_type        INTEGER      NOT NULL,  -- band tightness of the audited streak (percent): 1/5/10
    side            TEXT         NOT NULL,  -- 'top' (above-band excursion streak) | 'bottom' (below-band excursion streak)

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY ANCHOR date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_high_low_streaks PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'high_low_streaks', 16);

CREATE INDEX IF NOT EXISTS idx_high_low_streaks_forecast_id
    ON analysis_forecasts.high_low_streaks (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.high_low_streaks IS 'MA-Spread High/Low streak MEAN-MID anchor buckets (motivation): one row per forecast_id — every band-break excursion streak of analysis.mov_ave_high_low_pct_streaks whose anchor day (the ((day_count-1)//2 + 1)-th trading day of the span — the mean mid elapsed day once entered; an 8-day streak anchors its 4th day) falls inside the trailing 5-year window ending at the bucket''s stat_month contributes that ONE day as the bucket''s trigger. The anchor is EX-POST (the streak length is known only after the streak closes — an audit of streak-period behaviour, not a live trigger). side: top = above-band excursion (close on end_date above the end month''s high_val band), bottom = below-band (below low_val; unrounded close vs the stored band — the streaks step''s own test, ties to the nearer band). No cooldown (one trigger per streak; streaks are inherently separated by a 6+-day in-band gap or a side switch). Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (mean/std/max/min forward changes from the ANCHOR close at next/5d/20d/60d, occurrence counts, trigger_dates, reversal probabilities at the fixed 1% bar) live in analysis_forecasts.forecast_results via forecast_id; the config JSONB records mean/min/max day_count. Source: analysis.mov_ave_high_low_pct_streaks + analysis.mov_ave_high_low_pct (run python -m analyze.mov_ave_spread first).';
COMMENT ON COLUMN analysis_forecasts.high_low_streaks.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 5 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 5 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.high_low_streaks.band_period IS 'Band lookback window length (trading rows) of the audited band: 255 / 500 / 750 / 1275 — the streak row''s `period` in analysis.mov_ave_high_low_pct_streaks. (Named band_period, not period: ``period`` is reserved by the shared result-row pipeline — build_result_rows stamps forecast_results'' period next/5d/20d/60d onto every row dict.)';
COMMENT ON COLUMN analysis_forecasts.high_low_streaks.pct_type IS 'Band tightness parameter (percent) of the audited band: 1 / 5 / 10 — the streak row''s `pct_type` in analysis.mov_ave_high_low_pct_streaks.';
COMMENT ON COLUMN analysis_forecasts.high_low_streaks.side IS 'Bucket side: top = ABOVE-band excursion streak (the unrounded close on end_date exceeds the end month''s high_val band — reversals are forward changes below the row''s FIXED 1% threshold (0.01 — the period-end n-day close vs the anchor close)); bottom = BELOW-band excursion streak (close below low_val — reversals are changes above it). Mean-reversion semantics: the study (temp_scripts/study_high_low_streaks_forecast.py) shows below-band streaks drift UP and above-band streaks drift DOWN from the mid anchor.';
COMMENT ON COLUMN analysis_forecasts.high_low_streaks.is_market_hyped IS 'TRUE when ANY of the bucket''s ANCHOR dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
COMMENT ON COLUMN analysis_forecasts.high_low_streaks.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';

-- ----------------------------------------------------------------------------
--  Data-quality gate: the high_low_streaks vocabularies (shared helpers, see
--  01_forecast_results.sql / 00_partition_utils.sql). NOT VALID first,
--  validated once by the schema-wide sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_forecasts.high_low_streaks',
    'chk_high_low_streaks_side',
    $chk$side IN ('top', 'bottom')$chk$);
SELECT public.validate_pending_checks('analysis_forecasts');
