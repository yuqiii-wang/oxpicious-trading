-- ============================================================================
--  analysis.trends — the consolidated TRENDS + EPISODES component's SQL
--  (02_trend_episode_days.sql). See 01_trend_days.sql for the
--  component contract.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.trend_episode_days  (RSI tail-episode store)
--
--  One row per (sec_type, code, date) with AT LEAST ONE window in the
--  25% tails of the code's own full-history RSI rank distribution —
--  the RSI reading of the shared episode anatomy owned by
--  analysis.trends.episodes. REPLACES the retired
--  is_typical_{W}days / typical_ep_size_{W}days columns that rode
--  analysis.mov_ave_rsi (removed 2026-10-15): same column names and
--  semantics, owned by the trends component and keyed by THIS table's
--  PK — consumers LEFT JOIN it (a missing row = out-of-band for every
--  window), the RSI table itself carries only RSI values again.
--
--  SEMANTICS (the side-mirrored pass anatomy — the documented
--  contract of analysis.trends.episodes.rsi_tail_columns):
--    band        top = rank-ratio <= 25% of the code's own history
--                (rank 1 = the highest reading); bottom >= 75%.
--    pass        a maximal run of CONSECUTIVE in-band same-side days.
--    episode     passes bridged by 1..5 out-of-band TRADED days
--                (re-entries); > 5 out-days starts a fresh episode.
--    is_typical  BOTTOM band: TRUE the isolated FIRST-TOUCH passes
--                (the capitulation), FALSE the re-entry passes
--                (flickers / falling knives). TOP band: TRUE the
--                RE-ENTRY passes (the re-test — the double top),
--                FALSE the isolated first-touch passes (isolated
--                breakouts). Gap-1 continuation days inherit their
--                pass's flag. NULL = out-of-band for the window.
--    ep size     the episode's TOTAL same-side in-band trading-day
--                count across all its passes — both signal tiers'
--                population marker + the curve tier's 1/size episode
--                weight (each episode contributes total weight
--                exactly 1).
--
--  REBUILD SEMANTICS: band membership shifts with the full-history
--  rank distribution (the mov_ave_rsi_ranks rationale) — the store is
--  purged per sec_type (or per code) and CSV-COPY reinserted on every
--  ranks rebuild by analysis.trends.rsi_episode_days (hooked into the
--  mov_ave_spread RSI step's post-compute).
--
--  NOTE: NO FOREIGN KEYS / NO CHECK CONSTRAINTS by design (the NO-FK
--  derived-data convention of the mov_ave_spread suite).
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.trend_episode_days CASCADE;

CREATE TABLE analysis.trend_episode_days (
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- per-window side-mirrored pass flags (NULL = out-of-band)
    is_typical_3days  BOOLEAN,
    is_typical_6days  BOOLEAN,
    is_typical_10days BOOLEAN,
    is_typical_14days BOOLEAN,
    is_typical_20days BOOLEAN,
    is_typical_60days BOOLEAN,

    -- per-window episode size in trading days (NULL = out-of-band)
    typical_ep_size_3days  SMALLINT,
    typical_ep_size_6days  SMALLINT,
    typical_ep_size_10days SMALLINT,
    typical_ep_size_14days SMALLINT,
    typical_ep_size_20days SMALLINT,
    typical_ep_size_60days SMALLINT,

    CONSTRAINT pk_trend_episode_days PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed on code — same convention as the
-- sibling mov_ave_rsi tables so the wholesale DELETE per sec_type and
-- the per-code API reads walk the same partitioning.
SELECT public.create_hash_partitions('analysis', 'trend_episode_days', 16);

-- (sec_type, code, date) index — the sec_type-leading read shape the
-- shared-metrics block uses (LEFT JOIN one code's episode days);
-- same rationale as the mov_ave_rsi index.
CREATE INDEX IF NOT EXISTS idx_trend_episode_days_sec_type_code_date
    ON analysis.trend_episode_days (sec_type, code, date);

COMMENT ON TABLE  analysis.trend_episode_days IS 'RSI tail-episode days (the consolidated analysis.trends component''s store): one row per (code, sec_type, date) with >= 1 window in the 25% tails of the code''s own full-history RSI rank distribution. Per window: the SIDE-MIRRORED pass flag (is_typical_{W}days — BOTTOM 1.0 the isolated first-touch passes / TOP 1.0 the re-entry re-test passes; 0.0 the noisy population; gap-1 continuations inherit; NULL out-of-band) + the episode''s total in-band trading-day count (typical_ep_size_{W}days — both signal tiers'' population marker + the curve tier''s 1/size episode weight). Episodes bridge 1..5 out-of-band traded days; passes split at any exit. Replaces the retired is_typical columns on analysis.mov_ave_rsi; rebuilt WHOLESALE per sec_type on every mov_ave_rsi_ranks rebuild (band membership shifts with the full-history ranks).';
COMMENT ON COLUMN analysis.trend_episode_days.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.trend_episode_days.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.trend_episode_days.date IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.trend_episode_days.is_typical_14days IS 'The 14-day window''s side-mirrored pass flag (see the table comment for the anatomy). NULL = the day''s RSI rank ratio sits outside both 25% tails for the window.';
COMMENT ON COLUMN analysis.trend_episode_days.typical_ep_size_14days IS 'The 14-day window''s episode size: the episode''s total same-side in-band trading-day count across all its passes, carried by every in-band day. NULL out-of-band.';
