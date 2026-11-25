-- Daily market keyword tone from the audit matview: up-family minus
-- down-family kw_market mentions per day (the lexicon-tone cross-check on
-- the LLM sentiment curves). Code lists are the kw_market labels (0 px_up,
-- 2 sharp_up, 8 rebound / 1 px_dn, 3 sharp_dn, 9 pullback, 19 break_dn),
-- passed as args from the pipeline's KW_MARKET_CODE mirror so the SQL
-- carries no inline code map.
-- args:
--   pos_codes: value int[] — up-family kw_market codes
--   neg_codes: value int[] — down-family kw_market codes
SELECT EXTRACT(EPOCH FROM ka.date)::float8 AS date_epoch,
       SUM(ka.mentions) FILTER (WHERE ka.category_code = ANY({{pos_codes}}))::float8 AS pos_mentions,
       SUM(ka.mentions) FILTER (WHERE ka.category_code = ANY({{neg_codes}}))::float8 AS neg_mentions
FROM text.news_keyword_audit ka
WHERE ka.category_type = 1
  AND ka.date >= DATE '2019-06-01'
GROUP BY ka.date
ORDER BY ka.date
