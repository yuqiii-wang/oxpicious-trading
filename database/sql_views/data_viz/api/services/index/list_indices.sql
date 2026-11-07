-- Index browser list — all index_identity codes with date coverage, most
-- data first. HAVING COUNT(*) >= 40 filters out indices with fewer than 40
-- trading days (insufficient for visualization — matches the
-- "Insufficient data" alert threshold). Serves GET /api/index.
-- args: (none)

    SELECT code,
           MAX(name) AS name,
           COUNT(*)   AS n_days,
           MIN(date)::text AS first_date,
           MAX(date)::text AS last_date
      FROM stats.index_identity
     GROUP BY code
    HAVING COUNT(*) >= 40
     ORDER BY n_days DESC, code
