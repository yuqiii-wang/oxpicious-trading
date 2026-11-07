-- Ask AI insight metrics — the whole insight registry
-- (analysis_insight_metrics.insight_templates: per-tab/page metric specs
-- + generated db_keywords recipes), tab+page+ordinal order. The per-metric
-- VALUES resolve in the service / python resolver, not here.
-- Serves GET /api/analysis-insights/templates (10-min cache).
-- args: none
SELECT template_id, tab, page, metric, label, unit, description, spec, db_keywords, ordinal
         FROM analysis_insight_metrics.insight_templates
        ORDER BY tab, page, ordinal
