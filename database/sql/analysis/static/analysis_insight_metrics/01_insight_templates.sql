-- ============================================================================
--   analysis_insight_metrics — the Ask AI insight-templates registry.
--
--   One row per METRIC OF PARTICULAR INTEREST of one analysis page (a
--   chart page under one analysis tab). template_id is the stable identity
--     analysis.<tab-name>.<page-name>.<metric-name>
--   e.g. analysis.commons.ma-spread.rsi_14d.
--
--   SINGLE SOURCE OF TRUTH: the registry content lives in the
--   purpose-scoped sibling INSERT files (the static/label_dim DDL+data
--   convention), one per analysis tab:
--     02_insight_templates_commons.sql
--     03_insight_templates_derivatives.sql
--     04_insight_templates_composites.sql
--     05_insight_templates_signals.sql
--   Each file is its TAB's full re-sync: it DELETEs the tab's rows and
--   INSERTs the file's rows (removed metrics must not linger). Applying
--   all four re-syncs the registry. NEVER hand-INSERT rows here — edit
--   the matching tab file and re-apply, so the resolvers and the UI
--   (GET /api/analysis-insights/*) stay aligned. (Up to 2026-10-21 the
--   registry was authored in static/analysis_items.json and seeded by
--   `python -m analysis.analysis_insights seed`; the json path is
--   retired with the SQL-first migration — content proven identical.)
--
--   METRIC ORDERING: rows are ordered by TRADING VALUE within each page
--   (ordinal = the Ask AI modal's display order — the most
--   decision-relevant metric first). v2 (2026-10-21) put that principle
--   in place: MA60 gap metrics added to every price-anchor page (the
--   quarterly swing anchor — the measured ma60-cross/MA60-reclaim
--   trigger level), rsi_10d beside rsi_14d everywhere (the measured best
--   buy window), trend_state promoted to the top of signal-context
--   pages, corr_5d (the populated spot-vol fear axis of
--   analysis.options_vol_index_vs_spot_price_corr) added to the vol
--   pages, rr25 to the smile page, the margin trend direction to
--   margin-composite, gap MA5/half-life to the futures pages.
--
--   Resolution contract (mirrored by analysis/analysis_insight_metrics/resolver.py
--   and data_viz/api/services/analysis-insights.service.ts):
--     source.kind = 'table' — the row at the LATEST source."date" column
--       value <= the as-of date (latest available when no as-of),
--       equality-filtered by source."match" (a "$latest" match value =
--       MAX(col) within the filtered rows); value = source."value" column
--       of that row; delta = value − the previous-date row's value.
--       Optional source.order {by: date|value, dir: asc|desc} picks the
--       row (default date desc).
--     source.kind = 'compute' — value computed on demand by the resolver
--       (fn registry: close / pct_change / wilder_rsi / ma_gap_pct /
--       dist_from_high_low; args may pin code/sec_type).
--     A metric's top-level `compute` entry is the on-demand FALLBACK when
--     the table read yields no row/value.
--     `sec_types` lists the sec_type labels (etf/index/stock/industry) the
--     metric applies to; `scope` is security | industry | market.
--     pct_rank (every resolved row) — the RARITY quantile: the value's
--       percentile within its PRIOR trailing history, i.e. the share of
--       the metric's own prior observations (same scope filters, one row
--       per date, trailing 10y window ending at the value's own date)
--       at or below the value; 0.95 = the top 5% tail. pct_rank_n = the
--       prior sample size. NULL for non-numeric values or < 120 prior
--       observations; spec `"rank": false` opts out (categorical codes —
--       trend states, 0/1 flags — must not wear a fake quantile).
--       close/pct_change compute-kind metrics rank against their own
--       prior series computed from the baseline closes; the other
--       compute fns (rare fallbacks) leave the rank NULL.
--
--   DB-SEARCH RECIPES (v4, 2026-10-28): two keyword surfaces feed the Ask
--   AI's db search:
--     insight_templates.db_keywords — GENERATED (STORED) from spec.source:
--       {table, value, index: {date, code, sec_type}, conditions: match}.
--       It is the machine recipe for populating a SELECT over the metric's
--       table (which table, which indexed filter columns, which equality
--       conditions); it can never drift from spec because it is derived.
--       Empty object for compute-kind sources (nothing to search).
--     insight_page_docs.keywords — the per-page BUSINESS keyword array
--       (instrument / indicator / trade-idea terms), the human-vocabulary
--       counterpart for keyword search (GIN-indexed).
--   The sibling 02..05 data files also seed insight_page_docs (one doc row
--   per page, same DELETE+INSERT re-sync as the metrics).
--
--   No SMALLINT label-vocabulary column lives here (identity columns are
--   TEXT slugs; sec_type VALUES inside spec jsonb are resolved against
--   the existing 'sec_type' label domain by the resolvers), so no new
--   static.label_dim domain is introduced.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS analysis_insight_metrics;

-- The db-search recipe derivation, in ONE place. jsonb_build_object is
-- only STABLE (PG catalogs), so the STORED generated column needs an
-- IMMUTABLE wrapper — legitimate here: the expression is a pure jsonb
-- projection of spec, free of env-dependent output.
CREATE OR REPLACE FUNCTION analysis_insight_metrics.db_keywords_of(spec JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT jsonb_build_object(
        'table',      spec -> 'source' ->> 'table',
        'value',      spec -> 'source' ->> 'value',
        'index',      jsonb_build_object(
                          'date',     spec -> 'source' ->> 'date',
                          'code',     spec -> 'source' ->> 'code',
                          'sec_type', spec -> 'source' ->> 'sec_type'),
        'conditions', spec -> 'source' -> 'match')
$$;

CREATE TABLE IF NOT EXISTS analysis_insight_metrics.insight_templates (
    template_id  TEXT        PRIMARY KEY,  -- analysis.<tab>.<page>.<metric>
    tab          TEXT        NOT NULL,     -- analysis tab slug, e.g. 'commons'
    page         TEXT        NOT NULL,     -- analysis page slug, e.g. 'ma-spread'
    metric       TEXT        NOT NULL,     -- metric slug, e.g. 'rsi_14d'
    label        TEXT        NOT NULL,     -- concise UI label, e.g. 'RSI14'
    unit         TEXT,                      -- display unit ('%', 'd', '' …)
    description  TEXT,                      -- what to check / how to read it
    spec         JSONB       NOT NULL,     -- machine contract {scope, sec_types, source, compute}
    db_keywords  JSONB       GENERATED ALWAYS AS (analysis_insight_metrics.db_keywords_of(spec)) STORED,
    ordinal      SMALLINT    NOT NULL DEFAULT 0,  -- row order within the page (seed order)
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_insight_templates_page_metric UNIQUE (tab, page, metric),
    CONSTRAINT ck_insight_templates_id_shape
        CHECK (template_id = 'analysis.' || tab || '.' || page || '.' || metric)
);

-- Page-level doc registry: one row per analysis page (the Ask AI "doc
-- table" — the page description plus its business-keyword list). Same
-- authoring model as insight_templates: written ONLY by the sibling 02..05
-- per-tab data files (each a per-tab DELETE+INSERT re-sync).
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

CREATE INDEX IF NOT EXISTS idx_insight_templates_tab_page
    ON analysis_insight_metrics.insight_templates (tab, page, ordinal);

COMMENT ON SCHEMA  analysis_insight_metrics IS 'Ask AI insight registry: per-analysis-page metrics of particular interest, INSERT-seeded by the sibling per-tab data files (02..05 — the single source of truth, one file per analysis tab).';
COMMENT ON TABLE   analysis_insight_metrics.insight_templates IS 'One row per metric of particular interest of one analysis page; template_id = analysis.<tab>.<page>.<metric>. Written ONLY by the sibling 02..05 per-tab INSERT files (each a per-tab DELETE+INSERT re-sync; never hand-edited); values resolved per ask by the resolver / GET /api/analysis-insights/metrics.';
COMMENT ON COLUMN analysis_insight_metrics.insight_templates.template_id IS 'Stable identity analysis.<tab>.<page>.<metric>, e.g. analysis.commons.ma-spread.rsi_14d — the PK every consumer (py resolver, Express service, Ask AI modal) keys on.';
COMMENT ON COLUMN analysis_insight_metrics.insight_templates.spec IS 'Machine contract jsonb: {scope: security|industry|market, sec_types: [labels], source: {kind: table|compute, table, value, date, code, sec_type, match, order} | {kind: compute, fn, args}, compute: {fn, args}} — see the 01_ DDL header for the resolution semantics.';
COMMENT ON COLUMN analysis_insight_metrics.insight_templates.db_keywords IS 'Generated (STORED) db-search recipe derived from spec.source: {table, value, index: {date, code, sec_type}, conditions: match} — what table to search, by which indexed filter columns, with which equality conditions; the Ask AI flow reads it to populate a SELECT. Empty object for compute-kind sources.';
COMMENT ON TABLE analysis_insight_metrics.insight_page_docs IS 'One row per analysis page: the page-level ask description and its business keywords (keywords is the GIN-indexed search vocabulary). Written ONLY by the sibling 02..05 per-tab INSERT files, like insight_templates.';
COMMENT ON COLUMN analysis_insight_metrics.insight_page_docs.keywords IS 'Business keywords pertaining to the described page (instrument, indicator and trade-idea terms) — the Ask AI keyword-search vocabulary, GIN-indexed (idx_insight_page_docs_keywords).';
