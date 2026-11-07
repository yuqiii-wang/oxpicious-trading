-- Margin Trends — ALL broad-market index codes (stats.sec_index_tags,
-- BOOL_OR(is_broad_market) = TRUE) with display names from
-- stats.sec_classification, restricted to codes that HAVE
-- index_basic_stats rows (the DUMMY_* synthetic orphan-ETF parents are
-- tagged broad-market too but carry no OHLC). Drives the exchange
-- attribution's underlying dropdown; membership in this list is exactly
-- what the series endpoint's SQL accepts as ?underlying=.
-- Serves GET /api/analysis/margin-trends/underlyings
-- args: (none)
  WITH broad AS (
    SELECT code
    FROM stats.sec_index_tags
    GROUP BY code
    HAVING BOOL_OR(is_broad_market) = TRUE
  )
  SELECT b.code,
         COALESCE(NULLIF(sc.name, ''), b.code) AS name
  FROM broad b
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = b.code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  WHERE EXISTS (
    SELECT 1 FROM stats.index_basic_stats ibs WHERE ibs.code = b.code
  )
  ORDER BY b.code
