-- Per-contract day-CUMULATIVE traded volume at each 5-min bar (window sum
-- of the streamed bar volumes — the streamer's per-bar values are
-- day-cumulative deltas). SZSE / CFFEX contracts have no intraday bars and
-- are simply absent — their OI stays at the flat daily base.
-- args:
--   trade_date: value date — the target trading day
        SELECT contract_code, time,
               SUM(volume) OVER (
                   PARTITION BY contract_code
                   ORDER BY time
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               ) AS cum_vol
        FROM stats.options_intraday_5min
        WHERE date = {{trade_date}} AND volume IS NOT NULL
        ORDER BY contract_code, time
