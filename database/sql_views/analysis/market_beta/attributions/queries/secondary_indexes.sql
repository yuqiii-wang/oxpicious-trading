-- Industry-attributions step crash-safe index guard — the secondary
-- index names currently present on analysis.industry_attributions. The
-- incremental runner diffs this against config._SECONDARY_INDEXES and
-- repairs any index a force run dropped before being killed mid-rebuild
-- (the 2026-11-24 SIGKILL incident left the table PK-only, which every
-- later incremental run silently kept).
-- args: none
    SELECT indexname
    FROM pg_indexes
    WHERE schemaname = 'analysis'
      AND tablename = 'industry_attributions'
