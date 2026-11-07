-- Read-only analytics user: can SELECT the stats / analysis / signals /
-- strategy / static / live / text / llm data schemas only. No write, no DDL,
-- no role admin, no access to monitor / multi_media.
--
-- The password placeholder __READONLY_PASSWORD__ is substituted by the
-- apply script (temp_scripts/apply_readonly_role.py) — never commit a
-- real password here.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly_analytics') THEN
        CREATE ROLE readonly_analytics LOGIN PASSWORD '__READONLY_PASSWORD__';
    ELSE
        ALTER ROLE readonly_analytics WITH LOGIN PASSWORD '__READONLY_PASSWORD__';
    END IF;
END
$$;

-- Belt and suspenders: even if a grant is widened later, this session
-- default keeps the role from writing.
ALTER ROLE readonly_analytics SET default_transaction_read_only = on;

GRANT USAGE ON SCHEMA
    stats,
    analysis,
    analysis_signals,
    analysis_composites,
    analysis_insight_metrics,
    strategy,
    static,
    live,
    text,
    llm
TO readonly_analytics;

GRANT SELECT ON ALL TABLES IN SCHEMA
    stats,
    analysis,
    analysis_signals,
    analysis_composites,
    analysis_insight_metrics,
    strategy,
    static,
    live,
    text,
    llm
TO readonly_analytics;

GRANT SELECT ON ALL SEQUENCES IN SCHEMA
    stats,
    analysis,
    analysis_signals,
    analysis_composites,
    analysis_insight_metrics,
    strategy,
    static,
    live,
    text,
    llm
TO readonly_analytics;

-- Future tables created in these schemas are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA
    stats,
    analysis,
    analysis_signals,
    analysis_composites,
    analysis_insight_metrics,
    strategy,
    static,
    live,
    text,
    llm
GRANT SELECT ON TABLES TO readonly_analytics;

-- Deliberately NO grants to PUBLIC-facing roles and NO membership in any
-- other role: readonly_analytics stands alone.

-- The monitor schema carries blanket PUBLIC grants from an earlier setup;
-- revoke them so readonly users cannot reach it (readonly_analytics has
-- no direct grants on it).
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA monitor FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA monitor FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA monitor FROM PUBLIC;

-- Future monitor tables: drop the same default-privilege leak.
ALTER DEFAULT PRIVILEGES IN SCHEMA monitor REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA monitor REVOKE ALL ON SEQUENCES FROM PUBLIC;
