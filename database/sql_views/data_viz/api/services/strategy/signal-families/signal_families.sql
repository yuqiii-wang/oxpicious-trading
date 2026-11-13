-- The Singleton Strategy SIGNAL TAGS menu: the analysis signal families
-- with recorded standalone-tier trigger events for the sec_type
-- (analysis_signals.history_signals) — the families whose events a
-- strategy run can pick up (the 2026-11-07 signals-picked-up refactor:
-- the strategy tier NEVER detects, it only replays these events).
-- The menu is DATA-DRIVEN: a family appears once its events exist.
-- The stored sec_type / signal_type / action columns are SMALLINT label
-- codes — the filter carries the codes, the response decodes the family
-- name back to the label the UI/Python algo names speak.
-- Serves GET /api/strategy/singleton/signal-families.
-- args:
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "h.signal_type")
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   wait_code: fragment — LABEL_CODE.action.wait
--   hedge_pairs_code: fragment — LABEL_CODE.signal_type.hedge_pairs
SELECT {{signal_type_decode}} AS name,
       count(*)::int AS n_events,
       count(DISTINCT h.code)::int AS n_codes,
       max(h.date) AS latest_date
FROM analysis_signals.history_signals h
WHERE h.sec_type = {{sec_type_code}}
  -- standalone tier only: the TRUE tier fires inside its portfolio
  -- coordination, never as a standalone strategy signal
  AND h.is_in_portfolio = FALSE
  -- wait rows are observational only (never picked up by a strategy)
  AND h.action <> {{wait_code}}
  -- hedge_pairs is the PORTFOLIO family: its events sit at composite
  -- pair codes with no basic_stats OHLC series — not a standalone tag
  AND h.signal_type <> {{hedge_pairs_code}}
GROUP BY h.signal_type
ORDER BY name
