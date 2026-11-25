-- Per-(industry_id, date) bullish/bearish kw_market mention sums:
-- text.news_keywords ⋈ static.keyword_maps (category_type = market,
-- category_code in the bull/bear kw_market sets — static.label_dim
-- 'kw_market'). Negation is already handled upstream at extraction time
-- (negated occurrences never land in text.news_keywords.count). The side
-- split rides boolean-to-int casts, never CASE. industry_id '' = the
-- untagged articles (they join the '' broad-market row's sums).
-- args:
--   bull_codes: value int[] — bullish kw_market codes
--   bear_codes: value int[] — bearish kw_market codes
--   kw_category_market: value smallint — kw_category_type market code
SELECT COALESCE(nk.industry_id, '')             AS industry_id,
       EXTRACT(EPOCH FROM nk.date)::float8      AS date_epoch,
       SUM(((km.category_code = ANY({{bull_codes}}::smallint[]))::int4)
           * nk.count)::bigint                  AS bull_mentions,
       SUM(((km.category_code = ANY({{bear_codes}}::smallint[]))::int4)
           * nk.count)::bigint                  AS bear_mentions
FROM text.news_keywords nk
JOIN static.keyword_maps km ON km.keyword = nk.keyword
WHERE km.category_type = {{kw_category_market}}::smallint
GROUP BY 1, 2
