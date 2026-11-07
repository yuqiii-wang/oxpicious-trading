-- ----------------------------------------------------------------------------
--  UI DOCS — the authored UI description texts, in the STATIC schema
--  (static = the seeded reference data layer: rows land only via these
--  SQL files, never via a pipeline writer).
--
--  ONE row per described UI ELEMENT (a chart card's AI-Ask spec, a
--  button-group info popover, a table column's info mark). Every string
--  the UI shows as a DESCRIPTION — and every string the Ask AI modal
--  sends the LLM (intro / per-series semantics / notes) — lives here;
--  the TSX carries only the wiring (which doc, which runtime params).
--
--  TEMPLATE CONTRACT: intro / series.name / series.description /
--  notes[].text are TEMPLATES. {{param}} substitutes one runtime value;
--  {{#param}}...{{/param}} includes the section when the param is
--  truthy, {{^param}}...{{/param}} when falsy (flat — no nesting).
--  The UI resolver (data_viz/src/shared/docs) substitutes at render;
--  AI-Ask series NAMES must resolve byte-identically to the plotted
--  series names (derivePlotInfo matches semantics by name).
--
--  IDENTITY: doc_id = 'ui.<area>.<element>[.<part>]' — the stable slug
--  the TSX keys on. No categorical column (identity is the slug; the
--  data volume is a few hundred text rows served whole).
--
--  DATA: the purpose-scoped INSERT files under static/ (this file's
--  sibling directory) are the SOURCE OF TRUTH — each is its prefix's
--  full re-sync (DELETE the prefix's rows, INSERT the file's rows;
--  removed docs must not linger). NEVER hand-INSERT rows here — edit
--  the matching seed file and re-apply. The label arrays (bound_labels /
--  optional_labels) default to '{}' on rows whose INSERT omits them —
--  once a doc's labels are authored they ride the seed file's INSERT
--  list like every other column, so a re-sync never drops them.
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS static;

CREATE TABLE IF NOT EXISTS static.docs (
    doc_id     TEXT        PRIMARY KEY,  -- 'ui.<area>.<element>[.<part>]'
    title      TEXT,                     -- display label (popover / column titles; NULL for chart specs)
    intro      TEXT,                     -- the main description template
    series     JSONB,                    -- AI-Ask series catalog: [{"key","name","unit"?,"description"}] (templated)
    notes      JSONB,                    -- caveats: [{"key","text"}] (templated)
    bound_labels    TEXT[] NOT NULL DEFAULT '{}',  -- force-load vocabulary (exact prompt-label join)
    optional_labels TEXT[] NOT NULL DEFAULT '{}',  -- loose search vocabulary (budget-gated load)
    ordinal    SMALLINT    NOT NULL DEFAULT 0,  -- display order within the area
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_docs_id_shape CHECK (doc_id ~ '^ui\.[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)*$'),
    CONSTRAINT ck_docs_has_content CHECK (
        intro IS NOT NULL OR title IS NOT NULL OR series IS NOT NULL OR notes IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_docs_prefix ON static.docs (doc_id text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_docs_bound_labels    ON static.docs USING GIN (bound_labels);
CREATE INDEX IF NOT EXISTS idx_docs_optional_labels ON static.docs USING GIN (optional_labels);

COMMENT ON SCHEMA static IS 'Seeded reference data — static vocabularies and knowledge documents whose rows land ONLY via the SQL seed files under database/sql/static/ (never via a pipeline writer); re-apply a seed file to refresh its rows.';
COMMENT ON TABLE  static.docs IS 'Authored UI description texts — one row per described element (chart AI-Ask spec, info popover, column info). Seeded (and re-seeded) from database/sql/static/docs/static/ — the seed files are the source of record; never hand-UPDATE the rows. Served whole by GET /api/docs; the UI resolves the {{param}} templates at render.';
COMMENT ON COLUMN static.docs.doc_id IS 'Stable identity ui.<area>.<element>[.<part>], e.g. ui.ma-spread.panel, ui.ma-spread.group.pairsSimple, ui.signal-rules.col.valid — the slug the TSX docs store keys on.';
COMMENT ON COLUMN static.docs.intro IS 'The main description TEMPLATE: {{param}} substitutes a runtime value; {{#param}}..{{/param}} / {{^param}}..{{/param}} are truthy/falsy flat sections. Chart specs: this is the Ask-AI intro (also rendered by the modal''s description view).';
COMMENT ON COLUMN static.docs.series IS 'AI-Ask series catalog: [{"key": "rsi", "name": "RSI{{w}}", "unit": null, "description": "..."}] — the code picks the entries matching the ACTIVE overlays and resolves name/description with the same params the option builder used (names must match the plotted series verbatim).';
COMMENT ON COLUMN static.docs.notes IS 'Caveat templates: [{"key": "streakBridge", "text": "..."}] — the code includes the keys its current view needs, in ordinal order.';
COMMENT ON COLUMN static.docs.bound_labels IS 'Force-load keywords, ONE distinctive phrase per element, sharing the llm.ai_prompts bound_labels vocabulary. A summary run MUST LOAD the doc when the run''s effective prompt set (picked + question-force-loaded skills rows) carries any EXACT (case-insensitive equal) phrase from this array — the bridge folds the doc''s full content into the agent data (contract: ai-summary.service). ''{}'' = unbound. GIN-indexed (idx_docs_bound_labels).';
COMMENT ON COLUMN static.docs.optional_labels IS 'Optional labels in the insight_page_docs.keywords style (lowercase English/alias phrases). LOOSELY matched — a case-insensitive substring hit against the run''s question or the effective prompts'' label vocabulary — and loaded ONLY while the run stays within the bridge''s prompt budget (ai-summary.service DOC_OPTIONAL_PROMPT_CAP): too many prompts already selected, the loose docs skip. Bound docs always ride. ''{}'' = unlabeled. GIN-indexed (idx_docs_optional_labels).';
