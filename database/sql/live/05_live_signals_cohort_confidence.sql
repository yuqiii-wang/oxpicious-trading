-- ============================================================================
--  live.live_signals — OHLCV cohort add-on confidences (2026-09-28)
--
--  The signals tier registers the mov_rsi derived-population cohorts as
--  their OWN strategy rows (analysis_signals.signal_strategies with
--  ohlcv_type = 'ohlcv_boost' / 'ohlcv_selection', signal_sub_type
--  suffixed '@boost' / '@selection', per-bucket confidence = the
--  cohort's own chosen-rung sign-aligned dir_ave). Those rows are
--  decision/ranking records — 02_is_active keeps them permanently
--  is_active = FALSE, so they NEVER enter the live tier's threshold
--  set and are never breached themselves.
--
--  This migration denormalizes the SAME bucket's two cohort
--  confidences onto the parent's live breach record as ADD-ON context
--  (the live fetch's LEFT JOIN LATERAL probes the children by the full
--  parent identity: code, sec_type, signal_type, side, regime_state,
--  start_date, end_date + signal_sub_type = parent || '@boost' /
--  '@selection'). The record then carries THREE separate confidences —
--  the parent's (confidence) plus the boost-weighted and the
--  favorable-anatomy-subset cohort's — mirroring the recent-movements
--  forecast UI's parenthetical population add-on. NULL = the cohort
--  never registered for the bucket (its own gates did not pass) or the
--  family carries no ohlcv children (mov_std / the pair-cross
--  families). Display-only context: the live tier's breach decision
--  and the UI chip stay driven by the PARENT confidence alone.
-- ============================================================================

-- Existing installs gain the columns here (metadata-only ADD COLUMN,
-- nullable — only mov_rsi breaches can carry values); fresh installs
-- get them from the CREATE body in 03_live_signals.sql.
ALTER TABLE live.live_signals
    ADD COLUMN IF NOT EXISTS confidence_boost INTEGER,
    ADD COLUMN IF NOT EXISTS confidence_selection INTEGER;

COMMENT ON COLUMN live.live_signals.confidence_boost IS 'The SAME bucket''s ohlcv_boost cohort confidence (integer basis points) = ROUND(10000 × signal_strategies.confidence) of the @boost child row sharing the breached strategy''s (code, sec_type, signal_type, side, regime_state, start_date, end_date) — the ohlcv-score-trust-weighted measurement of the same trigger days (analyze.analysis_forecasts.ohlcv_boost), its own chosen-rung dir_ave, never blended into the parent confidence. ADD-ON context only: the child rows are permanently is_active = FALSE (never breached, never threshold-set) and this column never gates anything. NULL = no @boost child registered for the bucket, or a family without ohlcv children (mov_std / mov_pairs / mov_pairs_ema).';

COMMENT ON COLUMN live.live_signals.confidence_selection IS 'The SAME bucket''s ohlcv_selection cohort confidence (integer basis points) = ROUND(10000 × signal_strategies.confidence) of the @selection child row sharing the breached strategy''s identity — the favorable-anatomy subset''s own chosen-rung dir_ave (analyze.analysis_forecasts.ohlcv_selection; the streaks whose day-0 ohlcv score cleared the side''s bar — bottom side only, the top side carries no selection child). ADD-ON context only, same never-a-gate rule as confidence_boost. NULL = no @selection child registered (incl. every top-side breach) or a family without ohlcv children.';

-- ----------------------------------------------------------------------------
--  Data-quality gates (shared helpers, see 00_partition_utils.sql): the
--  0-10000 basis-point scale, mirroring chk_live_signals_confidence.
--  NULL passes (CHECK semantics) — non-mov_rsi rows legitimately carry
--  no cohort values. NOT VALID first, validated by the sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_conf_boost',
    $chk$confidence_boost IS NULL
       OR confidence_boost BETWEEN 0 AND 10000$chk$);
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_conf_selection',
    $chk$confidence_selection IS NULL
       OR confidence_selection BETWEEN 0 AND 10000$chk$);
SELECT public.validate_pending_checks('live');
