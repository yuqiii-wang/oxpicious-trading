-- ============================================================================
--  External resource audit — append-only request audit for the two external
--  resource families the repo depends on: HTTP downloads and LLM APIs.
--
--  One row per EXTERNAL REQUEST (not per retry attempt): the final
--  outcome — status code, time, request URL — plus is_failed /
--  failure_reason and the whole-call duration. Deliberately LEAN (no
--  payload, no headers, no latency histogram): this answers "which
--  request to which URL failed with what at what time", not "what did we
--  send". Heavy per-run state stays in the pipelines' own logs
--  (logs/<YYYY-MM-DD>/).
--
--  Writers (fire-and-forget through _common/external_resource_audit.py —
--  a bounded queue + one daemon writer thread per process; audit failure
--  never breaks the audited request):
--    HTTP — downloads/_common/net.py AntiBotProxy._request (the
--           consolidated anti-bot proxy behind every exchange downloader;
--           the legacy safe_get/safe_post wrappers route through it too)
--    LLM  — llm_agents/_core/transport.py RetryingJsonClient.post_json
--           (the retrying JSON POST transport shared by every provider)
--
--  PRIMARY KEY (service, id): service identifies the WRITING PROCESS by
--  its unique service name (the dotted module path — the same vocabulary
--  as monitor.service_audit.service / monitor.service_heartbeat.service),
--  id the bigserial row number. Per-process attribution is the whole
--  point of the table (which pipeline hammered which endpoint); the
--  per-date aggregation lives in monitor.external_resource_stats
--  (06_external_resource_stats.sql).
--
--  Type columns are SMALLINT label codes (the label-code contract,
--  stats.label_dim + _common/labels.py + data_viz/api/services/
--  label-codes.ts):
--    resource_type — domain 'ext_resource':    0 http | 1 llm
--    sub_type      — domain 'ext_http_source' when resource_type = 0
--                    (host family: sse / szse / bse / cffex / csindex /
--                     cnindex / chinabond / shibor / huatai / eastmoney /
--                     cninfo / gov / pboc / zhihu / other)
--                    domain 'ext_llm_provider' when resource_type = 1
--                    (provider name: zhipu / other)
--  service stays TEXT on purpose: it is an OPEN-ended vocabulary (every
--  pipeline module is a service; the label-code contract's explicit
--  exception) — same choice as monitor.service_audit.service.
--
--  DATE SEMANTICS: `date` is the GMT (UTC) day of created_at — a new
--  date starts at Beijing 08:00 (GMT midnight), so the overnight tail
--  between Beijing midnight and 8am counts to the PREVIOUS operational
--  day (most external traffic is that day's downloads / LLM runs
--  spilling past Beijing midnight). Writers cannot set it; the per-date
--  aggregation into monitor.external_resource_stats inherits the
--  boundary.
--
--  Applied after 04_service_audit.sql.
-- ============================================================================

-- Migration (2026-10-10): the generated date moved from an Asia/Shanghai
-- day to a GMT day (boundary Beijing 08:00). PG 17 SET EXPRESSION on the
-- stored generated column (one table rewrite); rows keep their created_at,
-- only the bucketing changes. Guarded on the current expression so it is
-- idempotent and a no-op on fresh installs (the CREATE below already
-- makes the GMT column).
DO $$
DECLARE
    gen_expr text;
BEGIN
    IF to_regclass('monitor.external_resource_audit') IS NULL THEN
        RETURN;
    END IF;
    SELECT pg_get_expr(adbin, adrelid) INTO gen_expr
    FROM pg_attrdef d
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = 'monitor.external_resource_audit'::regclass
      AND a.attname = 'date';
    IF gen_expr LIKE '%Asia/Shanghai%' THEN
        ALTER TABLE monitor.external_resource_audit
            ALTER COLUMN date SET EXPRESSION AS
                ((created_at AT TIME ZONE 'GMT')::date);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS monitor.external_resource_audit (
    id             bigserial    NOT NULL,
    -- DEFAULT 'unknown' is rolling-deploy insurance: a still-running
    -- pre-service-code process inserts WITHOUT this column and would
    -- otherwise lose every audit row to the NOT NULL constraint. New
    -- code always supplies the real name explicitly.
    service        text         NOT NULL DEFAULT 'unknown',  -- writing process's service name (dotted module path; 'unknown' = pre-service or stale-code writer)
    resource_type  SMALLINT     NOT NULL,  -- label code (stats.label_dim 'ext_resource'): 0 http | 1 llm
    sub_type       SMALLINT     NOT NULL,  -- label code (stats.label_dim): 'ext_http_source' row when http, 'ext_llm_provider' row when llm
    url            text         NOT NULL,  -- scheme://host/path — query string STRIPPED (anti-bot random params are noise)
    status_code    SMALLINT,               -- HTTP status of the final attempt; NULL = no response at all (transport error / timeout)
    is_failed      boolean      NOT NULL,
    failure_reason text,                   -- NULL when success; on failure a short reason (exception class+message / API error code) — never the payload
    duration_ms    integer,                -- wall time of the WHOLE logical call (retries + backoff included); NULL = not measured
    created_at     timestamptz  NOT NULL DEFAULT now(),
    -- GMT (UTC) day of created_at: a new date starts at Beijing 08:00
    -- (GMT midnight), so the Beijing 00:00–08:00 overnight tail counts
    -- to the previous operational day. GENERATED — writers never set it.
    date           date GENERATED ALWAYS AS ((created_at AT TIME ZONE 'GMT')::date) STORED,
    PRIMARY KEY (service, id)
);

CREATE INDEX IF NOT EXISTS external_resource_audit_recent_idx
    ON monitor.external_resource_audit (created_at DESC);

CREATE INDEX IF NOT EXISTS external_resource_audit_date_idx
    ON monitor.external_resource_audit (date);

-- Partial index for the failure-first queries (per-source failure sweeps,
-- "what broke in the last N hours").
CREATE INDEX IF NOT EXISTS external_resource_audit_failures_idx
    ON monitor.external_resource_audit (resource_type, sub_type, created_at DESC)
    WHERE is_failed;

COMMENT ON TABLE monitor.external_resource_audit IS
    'Append-only request audit for external resources: one LEAN row per '
    'external request (final outcome only — status, time, URL, duration, '
    'is_failed / failure_reason; no payloads), keyed (service, id) so '
    'every row names the process that made the request. resource_type / '
    'sub_type are SMALLINT label codes (stats.label_dim domains '
    'ext_resource / ext_http_source / ext_llm_provider). Written '
    'fire-and-forget by _common/external_resource_audit.py from '
    'downloads/_common/net.py (AntiBotProxy — every exchange downloader) '
    'and llm_agents/_core/transport.py (RetryingJsonClient — every LLM '
    'provider). Aggregated per (service, status_code, date) into '
    'monitor.external_resource_stats by '
    'python -m database.external_resource_stats.';

COMMENT ON COLUMN monitor.external_resource_audit.service IS
    'The writing process''s unique service name (dotted module path, e.g. '
    'builds.index.daily, downloads.stream.sse.price — the same vocabulary '
    'as monitor.service_audit.service / monitor.service_heartbeat.service; '
    'resolved in-process by _common/external_resource_audit.py from the '
    'service_heartbeat registration, falling back to the __main__ entry '
    'file, else ''unknown''). Stays TEXT on purpose: open-ended '
    'vocabulary (label-code contract exception). Half of the primary key.';

COMMENT ON COLUMN monitor.external_resource_audit.resource_type IS
    'The external resource family as its stats.label_dim (''ext_resource'' '
    'domain) SMALLINT code: 0 http (the downloads anti-bot proxy), 1 llm '
    '(llm_agents provider APIs).';

COMMENT ON COLUMN monitor.external_resource_audit.sub_type IS
    'Further classification within the family, as a stats.label_dim '
    'SMALLINT code — WHICH domain applies is selected by resource_type: '
    'http rows carry an ''ext_http_source'' code (host family: sse / szse '
    '/ bse / cffex / csindex / cnindex / chinabond / shibor / huatai / '
    'eastmoney / cninfo / gov / pboc / zhihu / other), llm rows an '
    '''ext_llm_provider'' code (zhipu / other). Decode joins label_dim on '
    '(domain, code).';

COMMENT ON COLUMN monitor.external_resource_audit.url IS
    'The request URL as scheme://host/path — the query string is STRIPPED '
    'at write time (anti-bot random params and long feed query params are '
    'noise; the host+path identify the endpoint). For LLM rows this is the '
    'provider endpoint (e.g. open.bigmodel.cn/api/paas/v4/chat/completions).';

COMMENT ON COLUMN monitor.external_resource_audit.status_code IS
    'HTTP status of the FINAL attempt (per logical request one row is '
    'written, retries are not itemized). NULL = no response at all — '
    'transport error, timeout, DNS failure.';

COMMENT ON COLUMN monitor.external_resource_audit.is_failed IS
    'TRUE when the request did not yield a usable response: non-2xx '
    'status, transport error, or (LLM) an API error body. FALSE = the '
    'final attempt succeeded.';

COMMENT ON COLUMN monitor.external_resource_audit.failure_reason IS
    'NULL on success (by contract — no success text is ever written). On '
    'failure a SHORT reason: exception class + message (HTTP) or the API '
    'error code + message (LLM), truncated at write time. Never the '
    'request/response payload.';

COMMENT ON COLUMN monitor.external_resource_audit.duration_ms IS
    'Wall time of the WHOLE logical call in milliseconds — every retry '
    'attempt and its backoff included, matching the one-row-per-logical-'
    'request grain (the final attempt''s duration alone is not recorded). '
    'NULL = not measured.';

COMMENT ON COLUMN monitor.external_resource_audit.created_at IS
    'Write-time timestamp (the writer''s now(), UTC-backed timestamptz).';

COMMENT ON COLUMN monitor.external_resource_audit.date IS
    'GENERATED: created_at bucketed to a GMT (UTC) day — a new date '
    'starts at Beijing 08:00 (GMT midnight), so the Beijing 00:00–08:00 '
    'overnight tail counts to the previous operational day. The '
    'denormalized grouping key the per-day stats '
    '(monitor.external_resource_stats) build on; writers cannot set it.';
