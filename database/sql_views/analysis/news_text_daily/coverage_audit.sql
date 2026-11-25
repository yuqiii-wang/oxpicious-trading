-- The panel's coverage basis — text.news_coverage_audit rows (built by
-- analysis.news_audit's CoverageAuditStep): per (trading day, industry)
-- article counts with '000001' as the whole-market rollup row. FAKES ARE
-- SUBTRACTED HERE (n_articles - n_fake — the fake-news detector verdicts
-- on text.news_digestions): every downstream calculation this basis feeds
-- (n_articles on the market rows, the flow_af abnormal-coverage flag, and
-- the pipeline's article-day target set) counts NON-FAKE coverage only;
-- the audit table itself keeps the raw counts (n_fake is its own audit
-- metric). Dates ride as epoch float8 (the cudf DB-read convention); the
-- counts are already calendar-rolled by the audit (weekend articles sit
-- on the next trading day).
-- args:
--   from_date: value date — earliest audit date to load (a warmup bound)
SELECT EXTRACT(EPOCH FROM c.date)::float8 AS date_epoch,
       c.industry_id,
       (c.n_articles - c.n_fake)::int4 AS n_articles
FROM text.news_coverage_audit c
WHERE c.date >= {{from_date}}::date
