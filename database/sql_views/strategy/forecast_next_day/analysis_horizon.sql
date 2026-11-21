-- The sec_type's ANALYSIS HORIZON — max(date) on analysis.
-- mov_ave_spreads_detail, the strategy runner's own horizon cap
-- (strategy/_common/runner.py: the backtest never runs past the
-- analysis build's last date). The forecast's strategy-freshness
-- check compares the nightly portfolio's end_date against
-- min(bar anchor, this horizon) — the dates the strategy CAN run —
-- so an analysis tier lagging behind the closes never triggers a
-- useless re-spawn the runner would cap anyway.
-- args:
--   detail_table: fragment — analysis.mov_ave_spreads_detail
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
SELECT max(date) AS horizon
FROM {{detail_table}}
WHERE sec_type = {{sec_type}}
