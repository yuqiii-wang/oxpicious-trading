-- Classification universe — the FULL industry-entity universe, one row
-- per entity from stats.industry_identity (the pool-'all' slice of
-- stats.industry_basic_stats; code = industry_id, name = industry_label),
-- latest name per code. Shared row source behind the full-universe
-- industry navs of the placeholder-pipeline analysis services
-- (market-crowdedness, hot-stock-momentum); cached under the shared
-- 10-min TTL (the view only moves on the nightly builds.industry).
-- Serves GET /api/analysis/market-crowdedness/* and
-- GET /api/analysis/hot-stock-momentum/* (codes/themes navs).
-- args: none
      SELECT DISTINCT ON (code) code, name
      FROM stats.industry_identity
      ORDER BY code, date DESC
