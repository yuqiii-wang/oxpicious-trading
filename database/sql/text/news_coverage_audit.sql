-- ============================================================================
--  Table: text.news_coverage_audit — the daily coverage audit
--  (per trading day per industry: how much news coverage happened)
--
--  ONE row per (date, industry_id): the article-count audit at the
--  DAILY grain (the 5-day provider/content audits keep their own
--  tables). industry_id '000001' is the WHOLE-MARKET ROLLUP row (every
--  article that trading day — the SSE Composite code, matching
--  text.news_text_daily's market scope_code); '' is the untagged
--  bucket; every other value is the stats.sec_classification
--  industry's own counts. Weekend/holiday articles roll FORWARD to the
--  next trading day (the analysis._common.calendar_roll convention —
--  the same alignment text.news_text_daily reads, T+1-compatible).
--
--  Columns: n_articles counts EVERY text.news row (digested or not,
--  fake-verdict or not — the raw coverage basis the daily panel's
--  abnormal-flow flag reads); n_digested counts rows with a digestion
--  (sentiment present or NULL verdict included); n_fake counts rows
--  whose digestion flagged is_fake_news.
--
--  Writer: analysis.news_audit's CoverageAuditStep (the --coverage
--  toggle; incremental by missing article-bearing trading day with the
--  audit's grace tail; --force truncates + full rebuild). The daily
--  panel (analysis.news_text_daily) READS this table for its
--  n_articles/flow basis — the 2026-11-22 coverage migration moved the
--  corpus scan out of the panel into the audit.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each trading day's news coverage audit per industry — article, digested and fake counts at the daily grain, with a whole-market rollup row.
-- tags: news coverage, 新闻覆盖, coverage audit, 覆盖审计, article count, 文章数量, daily audit, 每日审计, news volume, 新闻量, attention, 关注度, reference, 参考


CREATE TABLE IF NOT EXISTS text.news_coverage_audit (
    date         DATE    NOT NULL,  -- trading day (weekend articles roll forward to the next trading day)
    industry_id  TEXT    NOT NULL,  -- '000001' = the whole-market rollup row; '' = untagged articles; else the sec_classification industry_id
    n_articles   INTEGER NOT NULL,  -- every text.news row in scope (the raw coverage basis)
    n_digested   INTEGER NOT NULL,  -- rows with a text.news_digestions row
    n_fake       INTEGER NOT NULL,  -- rows whose digestion flagged is_fake_news
    n_industries INTEGER,           -- '000001' row only: distinct industries (incl. '' untagged) covered that day
    CONSTRAINT pk_news_coverage_audit PRIMARY KEY (date, industry_id)
);

CREATE INDEX IF NOT EXISTS idx_news_coverage_audit_industry
    ON text.news_coverage_audit (industry_id, date);

COMMENT ON TABLE text.news_coverage_audit IS
  'The daily news-coverage audit — per (trading day, industry): raw '
  'article counts, digested counts and fake-verdict counts, written by '
  'analysis.news_audit''s CoverageAuditStep (--coverage toggle). The '
  '''000001'' industry_id is the whole-market rollup row (the SSE '
  'Composite code, matching text.news_text_daily''s market '
  'scope_code); '''' is the untagged bucket. Weekend articles roll '
  'forward to the next trading day (the shared calendar-roll '
  'convention). The daily panel (analysis.news_text_daily) reads this '
  'table as its coverage/flow basis — the corpus scan lives HERE, not '
  'in the panel (the 2026-11-22 coverage migration).';

COMMENT ON COLUMN text.news_coverage_audit.date IS
  'The trading day the counts speak for. Articles publish on calendar '
  'dates; a weekend/holiday article rolls FORWARD to the next trading '
  'day (analysis._common.calendar_roll — T+1-compatible).';

COMMENT ON COLUMN text.news_coverage_audit.industry_id IS
  'The coverage scope: ''000001'' = the WHOLE-MARKET ROLLUP row (every '
  'article that day), '''' = untagged articles, anything else = the '
  'canonical sec_classification industry_id.';

COMMENT ON COLUMN text.news_coverage_audit.n_articles IS
  'Every text.news row in scope for that (day, industry) — digested or '
  'not, fake or not. The daily panel''s abnormal-flow flag '
  '(text.news_text_daily.flow_af) reads exactly this column.';

COMMENT ON COLUMN text.news_coverage_audit.n_industries IS
  '''000001'' rollup rows only: the number of DISTINCT industry_id '
  'buckets (including '''' untagged) that carried at least one article '
  'that day — the coverage-breadth evidence. NULL on the per-industry '
  'rows.';
