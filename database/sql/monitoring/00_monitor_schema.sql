-- ============================================================================
--  Monitoring Schema
--  Creates the `monitor` schema: current-state liveness + per-run audit
--  for the repo's service processes — the always-on downloads stream
--  pollers + the stream watchdog supervisor, and every batch pipeline
--  process (builds / downloads / analysis / strategy / live / llm_agents),
--  registered from _common/service_heartbeat.py — mirroring the live
--  schema conventions defined in database/sql/live/00_live_schema.sql.
--
--  Tables:
--    monitor.service_heartbeat — one row per service, keyed by its unique
--        service name (the dotted module path, e.g.
--        downloads.stream.sse.price, builds.stock, database.
--        stream_watchdog). Upserted every ~60s by a daemon thread inside
--        each process — the DB-persisted counterpart of the UI builds'
--        in-memory process-id-tag registry
--        (data_viz/api/services/py-runner.service.ts). Also carries
--        live_parallel, the per-service live-instance counter of the
--        max_parallel concurrency guard (the CAP itself is static JSON:
--        the service_config/ tree, default.json fallback).
--    monitor.service_audit    — append-only per-run history: start/end
--        time + exit reason for every registered process life, including
--        the watchdog restart trail (merged from the retired
--        monitor.stream_restart: a restart closes the dead life with a
--        'watchdog restart: …' exit_reason).
--    monitor.external_resource_audit — append-only request audit for the
--        external resource families (HTTP downloads, LLM APIs): one LEAN
--        row per external request, written fire-and-forget by
--        _common/external_resource_audit.py.
--    monitor.external_resource_stats — per-day aggregation of the audit:
--        one row per (service, status_code, date), computed by
--        python -m database.external_resource_stats.
--    monitor.db_health — append-only health samples for the primary/
--        replica Postgres pair (one row per instance per ~60s pass:
--        reachability, latency, DB vitals, WAL shipping state, container
--        CPU/mem), written by database/db_health.py.
--    monitor.db_table_audit — append-only per-table workload samples
--        (one row per (instance, table) per ~300s pass: read/write rates
--        + delta counts from pg_stat_user_tables cumulative counters,
--        live/dead + size snapshots, write_flag verdict), written by
--        database/db_table_audit.py.
--    monitor.db_table_audit_archive — COMPUTED daily rollup of the
--        audit: one record per (GMT day, table, instance) — day sums of
--        row/scan deltas, peak rates, worst write_flag, end-of-day
--        size snapshots — rebuilt idempotently per date by
--        database/db_table_audit_archive.py (daily on the maintainer
--        03:30 pass; no purge — this table IS the retention).
--    monitor.db_table_alert — append-only write-heavy alert trail: one
--        row per db_table_audit.write_flag STATE TRANSITION (raise /
--        downgrade / clear, severity + tripping rate + threshold),
--        written by database/db_table_audit.py.
--    monitor.caution_log — the cross-service caution trail: one OPEN row
--        per issue per service (deduped on (service, dedupe_key) over
--        open rows) — service errors, AI-found suspicions, manually-noted
--        pending bugs; severity-tagged and resolvable from the Monitor
--        UI's Cautions tab. Written fire-and-forget by
--        _common/caution_log.py + the data_viz API's
--        caution-log.service.ts.
--    monitor.sql_audit — one row per DISTINCT SQL statement executed on
--        the cluster, keyed by sha256 of the normalized text
--        (pg_stat_statements, delta-accumulated: calls / total / avg /
--        max exec time / rows, first seen / last active), upserted by
--        database/sql_audit.py.
--    monitor.src_data_audit — per-security source-data coverage audit:
--        one row per (sec_type, code) with n_days / first_date /
--        last_date, upserted by builds.classification (index from
--        stats.index_identity, stock from stats.stock_identity) and
--        builds.etf (etf rows from its OHLCV universe). Migrated
--        2026-10-10 out of the security classification JSON cache, whose
--        hand-editable shards should not carry build-time data.
--
--  Static JSON config (NOT tables — 10_static_json_config.sql dropped the
--  2026-10-09/10 config tables): two per-service DIRECTORY TREES under
--  static/ (the dotted service name is the path:
--  downloads/stream/szse/price.json ← downloads.stream.szse.price; the
--  tree-root default.json is each tree's fallback for services without
--  their own file). service_config/ holds each service's max_parallel
--  concurrency cap, its included/excluded_parallel_services cross-service
--  parallel-run lists (rooted dotted prefixes: a scope, a sub-scope path,
--  or the full service name; more specific wins on conflict — name >
--  sub-scope > scope; ties to excluded; empty included = unrestricted,
--  non-empty = whitelist — enforced at registration by
--  _common/service_heartbeat.py, previewed by the UI run guard) + its
--  supposed-run freq mirrored from scripts/*.sh
--  (shown on the Stream Monitor page's Pipeline processes table);
--  external_request_config/ holds the per-(service, request_type)
--  pacing / anti-bot request inventory — since 2026-10-10 it is the
--  RUNTIME source read by _common/request_config.py (AntiBotProxy
--  construction, sleep constants, poll loops, retry backoffs), with
--  net.py's constants as the last-resort fallback. Market holiday
--  calendars are NOT config — they live in database/sql/static/
--  holiday_calendar/ (one JSON per country, dates grouped by year),
--  read at import time by _common/_holidays_and_weekdays.py, which
--  fails loudly on a broken file (an empty calendar would corrupt
--  every pipeline's trading-day math).
--
--  Consumers:
--    database/stream_watchdog.py      — supervisor; restarts dead/stale
--                                       pollers, writes restart rows,
--                                       closes orphaned audit runs
--    database/monitoring.py --streams — exit-code alerts (default health
--                                       check includes it)
--    database/db_health.py            — the DB health sampler
--    database/db_table_audit.py       — the per-table workload audit +
--                                       write-heavy alert sampler
--    database/db_table_audit_archive.py — the audit's daily rollup
--                                       (maintainer 03:30 job + python -m)
--    database/sql_audit.py            — the per-statement SQL audit
--                                       sampler (pg_stat_statements)
--    data_viz/api/routes/monitor.ts   — GET /api/monitor/stream status page
--                                       + GET /api/monitor/src-data-audit
--                                       (Source Data tab's audit table)
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS monitor;

-- ----------------------------------------------------------------------------
-- Grants: monitor schema
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA monitor TO public;
GRANT USAGE ON SCHEMA monitor TO anon;
GRANT USAGE ON SCHEMA monitor TO authenticated;
GRANT USAGE ON SCHEMA monitor TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT ALL ON TABLES TO public;
ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT ALL ON SEQUENCES TO public;

ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA monitor GRANT ALL ON SEQUENCES TO service_role;

-- Ensure postgres has full privileges on any existing objects
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA monitor TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA monitor TO postgres;
