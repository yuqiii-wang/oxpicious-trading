-- News Text States — the recent STRONG-NEGATIVE industry event days
-- (kind 2, blended level <= -1.5 — the 2026-11-21 study's strong-neg
-- cut). The validated shape: these days were followed by positive 5d
-- industry returns (+1.8..+7.4% across AI/SEMI/COMMS; 6-13 events per
-- scope — suggestive, not proven) — the short-sale-constraint reversal
-- asymmetry. Newest first, capped.
-- industry_label is the canonical Chinese display name from the
-- classification (denormalized on stats.sec_classification); a code
-- with no classification row falls back to the raw code.
-- Serves GET /api/analysis/news-text-states/strong-neg-events
-- args:
--   window_days: value int — lookback window
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
      AND m.sent_level <= -1.5
      AND m.date >= CURRENT_DATE - {{window_days}}::int
    ORDER BY m.date DESC, m.scope_code
    LIMIT {{limit_rows}}::int
