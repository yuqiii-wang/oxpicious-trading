-- The Trading Signals page's date-selector roster (newest first): dates
-- present in live.live_signals UNION the newest 120 distinct daily-close
-- dates of the sec_type's basic_stats baseline — the selector stays
-- populated even when no live breach has been recorded yet.
-- Serves GET /api/live-data/trading-signals.
-- args:
--   sec_type: value text — the sec_type label ('index'|'etf'|'stock')
--   basic_stats_table: fragment — BASIC_STATS_TABLE[secType]
SELECT date FROM (
        SELECT date FROM live.live_signals WHERE sec_type = {{sec_type}}
        UNION
        SELECT date FROM (
            SELECT DISTINCT date FROM {{basic_stats_table}}
            WHERE close IS NOT NULL
            ORDER BY date DESC LIMIT 120
        ) roster
     ) u
     ORDER BY date DESC
     LIMIT 120
