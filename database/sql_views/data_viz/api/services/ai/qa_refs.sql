-- AI QA detail's per-ref resolutions joined back to their articles — the
-- feed card's click-to-expand refs (each carries a 4000-char content
-- preview for the inline expansion). `date` is ref_time's Asia/Shanghai
-- day; NULL ref_time renders no date on the card.
-- Serves GET /api/ai/qa (the refs block).
-- args:
--   qa_id: value bigint — the QA row whose refs to list
SELECT r.ref, r.ref_type, r.resolved_via, r.resolved_url, r.is_used,
           (r.ref_time AT TIME ZONE 'Asia/Shanghai')::text AS ref_time,
           n.news_id, n.title, n.source, n.url,
           LEFT(n.content, 4000) AS content,
           (r.ref_time AT TIME ZONE 'Asia/Shanghai')::date::text AS date
      FROM llm.llm_qa_refs r
      JOIN text.news n ON n.news_id = r.news_id
     WHERE r.qa_id = {{qa_id}}
     ORDER BY r.ref
