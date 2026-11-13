-- Related original articles for one Ask-AI summary item — the top
-- matches of the item's match terms over the recent window ending at
-- the run date, newest / most-voted first. Terms match the corpus's
-- OWN keyword index (text.news_keywords.keyword, equality — its terms
-- are derived, not literal substrings of the content) or the title
-- directly; the caller's fallback lane (keywords NULL) rides the
-- window's top-voted articles instead (the English page keywords never
-- match the Chinese corpus — date + industry still select). The
-- paragraphs column is the content's first non-empty paragraphs
-- (capped; a single-blob article degrades to its head) — the preloaded
-- excerpt the agent reads as init input, full bodies stay behind the
-- text_search tool.
-- Serves POST /api/ai/summary (the run data's analysis_items[].insight.
-- articles — the tags + paragraphs init input, see ai-summary.service).
-- args:
--   keywords: value text[] — the match terms (text.news_keywords
--     keyword equality OR title ILIKE); NULL = skip the keyword filter
--   industry_ids: value text[] — the industry scope filter; NULL =
--     market-wide, no industry filter
--   date_from: value date — window start (run date − N days)
--   date_to: value date — window end (the run date)
--   limit: value int — per-item cap (3)
SELECT n.title, n.source, n.date::text AS date, n.url, n.industry_id,
       LEFT((
         SELECT string_agg(p, chr(10) ORDER BY ord)
           FROM (SELECT p, ord
                   FROM unnest(string_to_array(n.content, chr(10)))
                        WITH ORDINALITY AS u(p, ord)
                  WHERE btrim(p) <> ''
                  ORDER BY ord
                  LIMIT 3) paras
       ), 700) AS paragraphs
  FROM text.news n
 WHERE n.date >= {{date_from}}::date
   AND n.date <= {{date_to}}::date
   AND ({{keywords}}::text[] IS NULL
        OR n.title ILIKE ANY({{keywords}}::text[])
        OR EXISTS (SELECT 1 FROM text.news_keywords nk
                    WHERE nk.news_id = n.news_id
                      AND nk.keyword = ANY({{keywords}}::text[])))
   AND ({{industry_ids}}::text[] IS NULL
        OR n.industry_id = ANY({{industry_ids}}::text[]))
 ORDER BY n.date DESC, n.votes DESC NULLS LAST, n.source ASC
 LIMIT {{limit}}::int
