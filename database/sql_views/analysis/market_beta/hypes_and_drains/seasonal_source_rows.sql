-- Hypes & drains SEASONAL (monthly) aggregation — stored per-date ranking
-- rows of analysis.industry_hypes_and_drains, fetched for standalone
-- re-runs when the pipeline's computed frame is not passed in. Only
-- non-NULL metric_value rows feed the monthly re-ranking (computed in
-- pandas afterwards).
-- args: none
            SELECT extract(epoch from date)::float8 AS date,
                   benchmark_code, period_days, weighting, rank_side,
                   industry_id, industry_label, metric_value::float8
                   AS metric_value
            FROM analysis.industry_hypes_and_drains
            WHERE metric_value IS NOT NULL
