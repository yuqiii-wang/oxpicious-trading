-- Per-article sentiment panel: every text.news row LEFT JOINed with its
-- digestion's sentiment_level. The digestible gate mirrors the digestion
-- work queue (content rows count as article flow; title-only placeholders
-- ride along with digestible=0 and never enter article_count). Dates ride
-- as epoch float8 (the cudf DB-read convention — python-date columns
-- poison frames). Full history — no date filter (config.FETCH_* rationale).
-- args: (none)
SELECT COALESCE(n.industry_id, '')                AS industry_id,
       EXTRACT(EPOCH FROM n.date)::float8         AS date_epoch,
       (n.content IS NOT NULL
        AND btrim(n.content) <> '')::int4         AS digestible,
       d.sentiment_level::float8                  AS sentiment_level
FROM text.news n
LEFT JOIN text.news_digestions d ON d.news_id = n.news_id
