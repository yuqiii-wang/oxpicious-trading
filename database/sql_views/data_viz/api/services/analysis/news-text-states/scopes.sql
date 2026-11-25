-- News Text States — the scope selector source: every (scope_kind,
-- scope_code) with rows in text.news_text_daily, with row counts and
-- date spans. The kind decodes through the label index
-- ('news_scope_kind' — never a CASE map); scope_label is the canonical
-- Chinese display name from the classification (industry_label for
-- kind 2, sector_label for kind 3 — denormalized on
-- stats.sec_classification; raw-code fallback when unmapped).
-- Serves GET /api/analysis/news-text-states/scopes
-- args:
--   scope_kind_decode: fragment — labelDecodeSql("news_scope_kind", "g.scope_kind")
    SELECT
      g.scope_kind,
      {{scope_kind_decode}} AS scope_kind_label,
      g.scope_code,
      COALESCE(i.industry_label, s.sector_label, g.scope_code) AS scope_label,
      g.rows,
      g.min_date,
      g.max_date
    FROM (
      SELECT
        m.scope_kind,
        m.scope_code,
        COUNT(*)    AS rows,
        MIN(m.date) AS min_date,
        MAX(m.date) AS max_date
      FROM text.news_text_daily m
      GROUP BY m.scope_kind, m.scope_code
    ) g
    LEFT JOIN (
      SELECT DISTINCT industry_id, industry_label FROM stats.sec_classification
    ) i ON g.scope_kind = 2 AND i.industry_id = g.scope_code
    LEFT JOIN (
      SELECT DISTINCT sector_id, sector_label FROM stats.sec_classification
    ) s ON g.scope_kind = 3 AND s.sector_id = g.scope_code
    ORDER BY g.scope_kind, g.scope_code
