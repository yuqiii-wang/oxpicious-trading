-- ============================================================================
--  Master Import Script — static reference data (cross-schema)
--  Execute all split SQL files in order.
--  Usage: psql -d analytics -f 00_init.sql
--
--  static/ holds seed/reference data not tied to one schema's tables.
--  Today that is the label-code contract (static.label_dim, the
--  closed-vocabulary label <-> SMALLINT code map): DDL in
--  01_label_dim.sql, data rows split into purpose-scoped INSERT files
--  (10..60) so each business area owns its own vocabulary rows; and
--  the JSON sibling ../holiday_calendar/ (one file per country, dates
--  grouped by year — cn.json A-share market closures migrated 2026-10-17
--  from _common/_holidays_and_weekdays.py inline constants; us.json
--  NYSE/Nasdaq and hk.json HKEX added the same day, verified against
--  the NYSE calendar and GovHK gazetted general holidays). The holiday
--  JSONs are NOT SQL and not part of this \ir chain — the Python
--  loader _common/_holidays_and_weekdays.py reads them at import time.
--  stats/00_init.sql includes this master, so a fresh install still
--  applies label_dim in the stats-master order.
--
--  FRESH INSTALL ONLY: 01_label_dim.sql DROPs the table. The live DB
--  gets new domains via INSERT ... ON CONFLICT (domain, code)
--  DO NOTHING from a temp_scripts/ apply script instead.
-- ============================================================================

\ir 01_label_dim.sql
\ir 10_market_state.sql
\ir 20_signal_detection.sql
\ir 30_industry_aggregation.sql
\ir 40_security_identity.sql
\ir 50_trading.sql
\ir 60_monitoring_infra.sql
