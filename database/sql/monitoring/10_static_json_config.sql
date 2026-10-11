-- ============================================================================
--  Static JSON config — the two config tables leave the DB.
--
--  monitor.service_config and monitor.external_request_config (created
--  2026-10-09/10 by the retired 08_service_config.sql /
--  09_external_request_config.sql) held CONFIG, not data: nothing in the
--  pipelines derives rows from them, both were browsable inventories read
--  by the UI. They now live as repo-checked-in JSON under this dir's
--  static/ subdir, so config changes are code-reviewed git edits instead
--  of out-of-band UPDATEs:
--
--    static/service_config/ — a per-service DIRECTORY TREE (same layout
--        as external_request_config/: the dotted service name is the
--        path, each file that service's ONE config object; the tree-root
--        default.json is the FALLBACK — cap 1 + freq on_demand for
--        services without their own file). Contents per file:
--        {service, max_parallel, freq, schedule, notes, source_ref,
--        updated_at}. max_parallel is the CAP — read at registration by
--        _common/service_heartbeat.py and by data_viz's static-config
--        service (both fall through to the default.json fallback /
--        constant 1 when the file is missing/unreadable, so a config
--        problem can never block a pipeline). freq is the SUPPOSED-RUN
--        cadence mirrored from scripts/*.sh (always | daily | monthly |
--        semiannually | once | occasionally), shown on the Stream
--        Monitor page's Pipeline processes table.
--        (Laid out as a flat service_config.json on 2026-10-10, rebuilt
--        as this tree the same day — the freq inventory wanted a
--        per-service home like the ext-request tree.)
--
--    static/external_request_config/ — a per-service DIRECTORY TREE
--        (the dotted service name is the path:
--        downloads/stream/szse/price.json ← downloads.stream.szse.price;
--        each file holds ONLY that service's rows). Contents per file:
--        the per-(service, request_type) pacing / anti-bot inventory,
--        migrated verbatim from the table (values + notes + source_ref +
--        per-row updated_at). The tree-root default.json is the FALLBACK
--        for services without their own file — the repo-wide 60s
--        anti-bot cooldown (lookups substitute the service's name; not
--        part of the inventory). As of 2026-10-10 the JSON is the
--        RUNTIME source of truth: _common/request_config.py resolves
--        each (service, request_type) row and every pacing consumer
--        (AntiBotProxy(service=...), sleep constants, poll loops, retry
--        backoffs) reads its interval from it; net.py's code constants
--        are the last-resort fallback for services with neither their
--        own file nor a usable default.json row. Keep the rows in sync
--        with deliberate code-side overrides (e.g. the exchange
--        adapters' documented rotate_browser_profile=False).
--
--  What could NOT go to JSON is the one runtime-state column the config
--  table carried: live_parallel (instances currently believed alive —
--  mutated on every registration/exit by many processes; a JSON file is
--  not a race-safe counter). It moves to monitor.service_heartbeat, the
--  per-service CURRENT-state row — its natural home, and the row the
--  claim's conditional UPDATE already serializes on.
--
--  Deploy note: processes still running the OLD code hold their slots in
--  service_config; after this migration their exit releases fail (caught,
--  logged, harmless). Restart the always-on services after applying — or
--  let the stream watchdog's pid-liveness reset self-heal the counters on
--  their next detected death, the same story as the 08 migration.
--
--  Applied after 09_external_request_config.sql. Idempotent.
-- ============================================================================

-- live_parallel moves home: config table → heartbeat row.
ALTER TABLE monitor.service_heartbeat
    ADD COLUMN IF NOT EXISTS live_parallel smallint NOT NULL DEFAULT 0;

COMMENT ON COLUMN monitor.service_heartbeat.live_parallel IS
    'Instances currently believed alive: +1 on a registration that claims '
    'a slot past the max_parallel cap (the cap is static JSON config — '
    'database/sql/monitoring/static/service_config/), -1 (floor 0) on '
    'a recorded exit, RESET from host pid+cmdline liveness by the '
    'heartbeat watchdog when a process is detected down without an exit '
    'record. Migrated 2026-10-10 from monitor.service_config.';

-- Carry the live counts over BEFORE the drop (a running service always
-- has a heartbeat row — its beat thread upserts it — so an UPDATE join
-- covers every nonzero counter; config rows without a heartbeat row are
-- crashed-while-claiming leftovers whose counts are 0 by definition).
DO $$
BEGIN
    IF to_regclass('monitor.service_config') IS NOT NULL THEN
        UPDATE monitor.service_heartbeat h
        SET live_parallel = c.live_parallel
        FROM monitor.service_config c
        WHERE h.service = c.service;
    END IF;
END $$;

-- The config tables are gone — the static/service_config/ tree and the
-- static/external_request_config/ tree replace them.
DROP TABLE IF EXISTS monitor.service_config;
DROP TABLE IF EXISTS monitor.external_request_config;
