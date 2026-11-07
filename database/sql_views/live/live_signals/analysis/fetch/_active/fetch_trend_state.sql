-- The code's TREND code at the latest signal-rules-tier trend_days row
-- at-or-before the date (the day-context cell-match input; None when the
-- store has no row — no cell can then match).
-- args:
--   trend_days_table: fragment — the trend-days store (TREND_DAYS_TABLE)
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   code: value text — the ticker
--   on_date: value date — bound the lookup at-or-before this day

        SELECT trend FROM {{trend_days_table}}
        WHERE sec_type = {{sec_type}} AND code = {{code}} AND date <= {{on_date}}
        ORDER BY date DESC LIMIT 1
