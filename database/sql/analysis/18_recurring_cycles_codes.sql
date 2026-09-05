-- ============================================================================
--  Recurring Cycles — populated-codes registry.
--
--  analysis.recurring_cycles_codes: one row per (sec_type, code) that has
--  rows in analysis.recurring_cycles. Maintained by the Python populator
--  (analyze.recurring_cycles) as it writes the main table:
--    --force          → full replace of the sec_type's registry rows
--    incremental      → upsert of the codes whose rows were written
--    --code (single)  → delete + re-register that one code
--
--  WHY: the recurring_cycles table is 55 GB (per-row spectra arrays, 32
--  hash partitions keyed by code). The UI page-load endpoints used to
--  answer "which codes have data" with SELECT DISTINCT code over the
--  whole table (~1.1s per endpoint, every page load). This registry
--  answers it in a few ms via the (sec_type, code) PK, and keeps every
--  page-load query off the big table — all recurring_cycles reads stay
--  code-filtered (PK-index-driven) as required by the UI contract.
--
--  All INSERTs happen in Python per project rule; this file only defines
--  the structure. Backfill for existing deployments: run the Python
--  populator once (any mode re-registers processed codes) or the one-off
--  backfill script.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis.recurring_cycles_codes (
    sec_type TEXT NOT NULL,
    code     TEXT NOT NULL,

    CONSTRAINT pk_recurring_cycles_codes PRIMARY KEY (sec_type, code),
    CONSTRAINT chk_recurring_cycles_codes_sec_type
        CHECK (sec_type IN ('index', 'etf', 'stock'))
);

COMMENT ON TABLE analysis.recurring_cycles_codes IS
    'Populated-codes registry for analysis.recurring_cycles: one row per (sec_type, code) present in the main table. Maintained by analyze.recurring_cycles (force = full replace per sec_type; incremental/single-code = upsert of written codes; single-code wipe = delete). Lets the UI page-load endpoints (themes / strategy-themes) resolve "codes with recurring-cycles data" from a ~500-row table instead of SELECT DISTINCT over the 55 GB partitioned recurring_cycles table on every page load. All recurring_cycles data reads remain code-filtered (PK (code, sec_type, last_date, range_days) driven). All INSERTs in Python per project rule.';
