-- News Text States — the cross-sectional recent levels: per industry
-- (kind 2) and sector (kind 3) scope, its LATEST event-day row within
-- the lookback window (DISTINCT ON — the scope's most recent reading,
-- the current cross-section the page's bar overview plots). Scopes
-- with no event-day row in the window drop out (no reading to plot);
-- scopes WITH one keep exactly one row even if they had several event
-- days in the window.
-- scope_label is the canonical Chinese display name from the
-- classification (industry_label for kind 2, sector_label for kind 3 —
-- denormalized on stats.sec_classification); a code with no
-- classification row falls back to the raw code.
-- Serves GET /api/analysis/news-text-states/recent-levels
-- args:
--   window_days: value int — lookback window (calendar days back from
--                CURRENT_DATE, the same convention as strong_neg_events)
SELECT DISTINCT ON (m.scope_kind, m.scope_code)
  m.scope_kind,
  m.scope_code,
  COALESCE(i.industry_label, s.sector_label, m.scope_code) AS scope_label,
  m.date,
  m.n_scored,
  m.sent_level,
  m.flow_pos,
  m.flow_neg,
  m.low_coverage
FROM text.news_text_daily m
LEFT JOIN (
  SELECT DISTINCT industry_id, industry_label FROM stats.sec_classification
) i ON m.scope_kind = 2 AND i.industry_id = m.scope_code
LEFT JOIN (
  SELECT DISTINCT sector_id, sector_label FROM stats.sec_classification
) s ON m.scope_kind = 3 AND s.sector_id = m.scope_code
WHERE m.scope_kind IN (2, 3)
  AND m.sent_level IS NOT NULL
  AND m.date >= CURRENT_DATE - {{window_days}}::int
ORDER BY m.scope_kind, m.scope_code, m.date DESC
