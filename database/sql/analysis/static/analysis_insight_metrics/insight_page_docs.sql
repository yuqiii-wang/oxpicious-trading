
-- Page-level doc registry: one row per analysis page (the Ask AI "doc
-- table" — the page description plus its business-keyword list). Same
-- authoring model as insight_templates: written ONLY by the sibling 02..05
-- per-tab data files (each a per-tab DELETE+INSERT re-sync).
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per analysis page — the page's ask description and its business keywords.
-- tags: insights, 洞察, page docs, 页面说明, keywords, 关键词

CREATE TABLE IF NOT EXISTS analysis_insight_metrics.insight_page_docs (
    tab        TEXT        NOT NULL,  -- analysis tab slug, e.g. 'commons'
    page       TEXT        NOT NULL,  -- analysis page slug, e.g. 'ma-spread'
    title      TEXT        NOT NULL,  -- UI page title, e.g. 'MA-Spread'
    doc        TEXT,                  -- page-level ask description (AI-prompt fragment)
    keywords   TEXT[]      NOT NULL DEFAULT '{}'::text[],  -- biz keywords of the page
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tab, page)
);

CREATE INDEX IF NOT EXISTS idx_insight_page_docs_keywords
    ON analysis_insight_metrics.insight_page_docs USING GIN (keywords);

COMMENT ON SCHEMA  analysis_insight_metrics IS 'Ask AI insight registry: per-analysis-page metrics of particular interest, INSERT-seeded by the sibling per-tab data files (02..05 — the single source of truth, one file per analysis tab).';
COMMENT ON TABLE analysis_insight_metrics.insight_page_docs IS 'One row per analysis page: the page-level ask description and its business keywords (keywords is the GIN-indexed search vocabulary). Written ONLY by the sibling 02..05 per-tab INSERT files, like insight_templates.';
COMMENT ON COLUMN analysis_insight_metrics.insight_page_docs.keywords IS 'Business keywords pertaining to the described page (instrument, indicator and trade-idea terms) — the Ask AI keyword-search vocabulary, GIN-indexed (idx_insight_page_docs_keywords).';
