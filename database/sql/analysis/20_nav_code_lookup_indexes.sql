-- ============================================================================
--  Nav code-lookup indexes — analysis.pe / analysis.dividends
--
--  The classification nav trees on the PE page (listPeThemes /
--  listPeStrategyThemes → META_SQL) derive their code universe via
--  `SELECT DISTINCT code FROM analysis.pe WHERE sec_type = $1 UNION
--  SELECT DISTINCT code FROM analysis.dividends WHERE sec_type = $1`.
--  The tables are hash-partitioned by code with PK (code, sec_type, date),
--  so that DISTINCT had no usable index path (sec_type is NOT a PK prefix)
--  and parallel-scanned every partition — ~1.2s on the stock universe,
--  paid on every cold nav load (2026-09-29).
--
--  These (sec_type, code) indexes give the DISTINCT an index-only scan per
--  partition (plus help any other sec_type+code equality probe). Write cost
--  on the pipeline's bulk inserts is negligible relative to the COPY loads.
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_pe_sec_type_code
    ON analysis.pe (sec_type, code);

CREATE INDEX IF NOT EXISTS idx_dividends_sec_type_code
    ON analysis.dividends (sec_type, code);
