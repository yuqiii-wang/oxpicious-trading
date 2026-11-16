-- SQL write action — analysis.market_crowdedness_hot_sets DELETE
-- (replace a re-derived year roster for exactly the pools this pass
-- re-derived). Executed by analysis/market_crowdedness/pipeline.py
-- (incremental runs: the open year's roster re-derives nightly until
-- the year closes; backfill runs re-derive gap years for gap pools —
-- frozen pools outside the derivation keep their rows).
--
-- Params (asyncpg $n, in order):
--   $1 sec_type   SMALLINT — the scoped static.label_dim sec_type code
--   $2 hot_year   INT — the year whose derived roster is replaced
--   $3 codes      TEXT[] — the pool codes this pass re-derived for the year
DELETE FROM analysis.market_crowdedness_hot_sets
WHERE sec_type = $1::smallint
  AND hot_year = $2::int
  AND code = ANY($3::text[])
