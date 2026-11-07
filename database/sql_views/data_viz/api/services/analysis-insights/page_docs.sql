-- Ask AI insight metrics — all insight page docs
-- (analysis_insight_metrics.insight_page_docs: title / doc / biz keywords
-- per tab+page), tab+page order. Seeded by the per-tab data files under
-- database/sql/analysis/static/analysis_insight_metrics/.
-- Serves GET /api/analysis-insights/page-docs (10-min cache).
-- args: none
SELECT tab, page, title, doc, keywords
         FROM analysis_insight_metrics.insight_page_docs
        ORDER BY tab, page
