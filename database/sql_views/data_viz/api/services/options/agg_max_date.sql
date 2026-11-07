-- Aggregate-cache version anchor — MAX(options_identity.date) (index-only,
-- ~1ms; memoized 30s). The version-keyed aggregate cache in
-- options.service.ts keys its memoized per-plot responses on it.
-- args: (none)
SELECT MAX(date) AS d FROM stats.options_identity
