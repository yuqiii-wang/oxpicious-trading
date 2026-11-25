-- News Text States — the recent industry EVENT DAYS across industries
-- (scope_kind 2 rows: a row exists only on days the industry has scored
-- articles — the validated event-study framing). Newest first, capped.
-- industry_label is the canonical Chinese display name from the
-- classification (denormalized on stats.sec_classification); a code
-- with no classification row falls back to the raw code.
-- Serves GET /api/analysis/news-text-states/events
-- args:
--   limit_rows: value int — max rows returned
    SELECT
      m.date,
      m.scope_code        AS industry_id,
      COALESCE(i.industry_label, m.scope_code) AS industry_label,
      m.n_articles,
      m.n_scored,
      m.sent_level,
      m.flow_pos,
      m.flow_neg,
      m.low_coverage
    FROM text.news_text_daily m
    LEFT JOIN (
      SELECT DISTINCT industry_id, industry_label FROM stats.sec_classification
    ) i ON i.industry_id = m.scope_code
    WHERE m.scope_kind = 2
    ORDER BY m.date DESC, m.scope_code
    LIMIT {{limit_rows}}::int
