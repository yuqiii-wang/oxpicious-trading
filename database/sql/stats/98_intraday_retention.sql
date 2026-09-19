-- ============================================================================
--  Intraday retention — stats.{etf,index,stock}_intraday_5min
--
--  OPT-IN MAINTENANCE — run manually, NEVER automatically:
--      psql -U postgres -d "oxpicious-stats" -f stats/98_intraday_retention.sql
--
--  The 5-minute bar tables grow unbounded (append-only, HASH(code)
--  partitions with no time dimension). The live-signals tier consumes only
--  the LATEST bar per code (python -m live.live_signals), and the on-demand
--  as-of replay bounds its sources to a requested date — both need only
--  recent history. This file prunes bars older than the retention window
--  below; adjust the constant and re-run as needed.
--
--  NOT wired into any init/pipeline path: deleting trading data is an
--  explicit operator decision. 2026-09-18: prepared by the SQL-cleanup
--  pass, NOT yet executed (bar older than RETENTION_DAYS at write time
--  remain until the first manual run).
-- ============================================================================

DO $$
DECLARE
    -- Retention window in CALENDAR days (bars with date < today - N are
    -- purged). 90 days of A-share trading ≈ 61 sessions × ~48 bars ≈ 2.9K
    -- rows per code — ample for the live tier and any recent replay.
    retention_days int := 90;
    cutoff         date := current_date - retention_days;
    r              record;
    deleted        bigint := 0;
BEGIN
    FOR r IN
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'stats'
          AND table_name IN ('etf_intraday_5min',
                             'index_intraday_5min',
                             'stock_intraday_5min')
        ORDER BY table_name
    LOOP
        EXECUTE format(
            'DELETE FROM stats.%I WHERE date < $1',
            r.table_name)
        USING cutoff;
        GET DIAGNOSTICS deleted = ROW_COUNT;
        RAISE NOTICE '%: purged % rows older than %',
            r.table_name, deleted, cutoff;
    END LOOP;
END $$;
