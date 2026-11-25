-- Per (article, industry) CO-MENTION frame for the industry/sector
-- event-day scopes: every substantial mention of a catalog industry.
-- The article's matched industry keywords (text.news_keywords) join
-- the static.keyword_maps industry rows (category_type=0 — the
-- keyword's own mapped industry, NOT the article's single primary
-- industry_id) and aggregate per (news_id, industry_id) with a
-- substantiality bar: sum(count * length(keyword)) >= min_kw_chars —
-- one 4-char keyword, or two 2-char hits; a single incidental 2-char
-- mention does not attribute the article to the industry. One row per
-- (article, industry) even when several of the industry's keywords
-- matched. Score columns join for the blend downstream; broad-market
-- recap PRIMARIES (BROAD_SSE) are excluded here — recap bodies
-- name-drop every theme (the recap scope itself is kind 1; the
-- industry scope additionally drops BROAD_SSE mentions downstream,
-- while the sector scope keeps them mapped to the BROAD parent).
-- args:
--   from_date: value date — earliest publish date to load (a warmup bound)
--   min_kw_chars: value int — the substantiality bar
SELECT nk.news_id,
       km.industry_id,
       EXTRACT(EPOCH FROM n.date)::float8           AS date_epoch,
       n.source,
       d.sentiment_level::float8                    AS sentiment_level,
       s.lex_score::float8                          AS lex_score,
       COALESCE(s.disagrees, false)::int4           AS disagrees
FROM text.news_keywords nk
JOIN static.keyword_maps km
  ON km.keyword = nk.keyword
 AND km.category_type = 0
 AND km.industry_id IS NOT NULL
JOIN text.news n ON n.news_id = nk.news_id
JOIN text.news_digestions d ON d.news_id = n.news_id
LEFT JOIN text.news_lexicon_scores s ON s.news_id = n.news_id
WHERE n.date >= {{from_date}}::date
  AND d.sentiment_level IS NOT NULL
  AND COALESCE(d.is_fake_news, false) = false
  AND COALESCE(n.industry_id, '') <> 'BROAD_SSE'
GROUP BY nk.news_id, km.industry_id, n.date, n.source,
         d.sentiment_level, s.lex_score, COALESCE(s.disagrees, false)
HAVING sum(nk.count * length(nk.keyword)) >= {{min_kw_chars}}::int
