-- Hypes & Drains — the benchmark's display name (latest
-- stats.index_identity row; empty response falls back to the code).
-- Serves GET /api/analysis/industry-hypes-and-drains.
-- args:
--   code: value text — the benchmark index code
    SELECT DISTINCT ON (code) name
    FROM stats.index_identity
    WHERE code = {{code}}::text
    ORDER BY code, date DESC
    LIMIT 1
