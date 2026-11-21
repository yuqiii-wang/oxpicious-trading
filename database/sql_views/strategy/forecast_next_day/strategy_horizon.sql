-- The run's codes' LATEST ACTIVE strategy runs' end_dates — the
-- forecast's STRATEGY FRESHNESS input: when the nightly mixed
-- portfolio's latest run is behind the OHLCV bar horizon (new bars
-- arrived since), the forecast module spawns the strategy CLI first
-- (the trainer's post-training run precedent) so the forecast reading
-- applies to a current position state. strategy_identity carries
-- sec_type as TEXT (the strategy schema's legacy convention).
-- args:
--   strategy_name: value text — the portfolio strategy the reading applies to
--   sec_type_label: value text — the sec_type label ('index')
--   codes: value text[] — the run's codes
SELECT DISTINCT ON (i.code) i.code, i.end_date
FROM strategy.strategy_identity i
WHERE i.strategy_name = {{strategy_name}}
  AND i.sec_type = {{sec_type_label}}
  AND i.code = ANY({{codes}})
  AND i.is_active
ORDER BY i.code, i.end_date DESC
