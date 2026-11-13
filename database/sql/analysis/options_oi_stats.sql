
-- Pre-expiry contrarian metrics on the gap (skewness − neutral): the
-- recency-aware trio, comparable across expiry groups (the legacy
-- cumulative counter was not carried over). Columns are 0/NULL until
-- rebuilt: run `python -m analysis.options --force` (incremental mode only
-- fills MISSING groups, so it would not refresh existing rows).

-- Dedicated per-expiry OI stats — analysis.options_oi_stats.
--
-- Unlike the sibling expiry tables, rows are keyed by the REAL expiry
-- date (NOT the collapsed open-group convention): the per-expiry
-- tooltips need per-contract-expiry OI levels/changes, which the pooled
-- open-group collapse (all currently-open expiries of a
-- (option_type, underlying) merged into one mean-dated bucket) cannot
-- represent. The collapsed identity table therefore cannot host these
-- keys for open groups — hence NO FK to options_expiry_identity (the
-- same exemption as options_vol_index, for the mirror reason).
-- option_type is kept in the PK: CALL and PUT rows of a group hold the
-- SAME value (the stats pool calls + puts), matching the sibling
-- tables' duplicated-row convention.
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Open-interest levels and changes per expiry group — daily totals, 5/20-session changes and 20-session highs.
-- tags: options, 期权, open interest, 持仓量, expiry, 到期


CREATE TABLE IF NOT EXISTS analysis.options_oi_stats (
    date                      DATE          NOT NULL,
    option_type               SMALLINT      NOT NULL,  -- label code (static.label_dim 'option_type'): 0=CALL 1=PUT
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    oi_total                  NUMERIC(14,2),   -- calls+puts OI of the group on the date (contracts)
    oi_delta_5d               NUMERIC(14,2),   -- oi_total - oi_total 5 trading sessions earlier (NULL when the group had no row then)
    oi_delta_20d              NUMERIC(14,2),   -- oi_total - oi_total 20 trading sessions earlier
    oi_max_20d                NUMERIC(14,2),   -- max oi_total over the trailing 20 sessions incl. the date (NULL when none)

    CONSTRAINT pk_options_oi_stats
        PRIMARY KEY (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_oi_stats', 8);

-- idx_options_oi_stats_underlying_date (underlying_code, date) dropped:
-- a prefix of the underlying_code-first PK, which already serves per-underlying lookups.

CREATE INDEX IF NOT EXISTS idx_options_oi_stats_expiry
    ON analysis.options_oi_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_oi_stats                       IS 'Dedicated per-expiry options OI statistics (per-real-expiry-date — NOT the collapsed open-group convention; see the header note). Per (underlying_code, date, option_type, expiry_date) row: oi_total (calls+puts OI of the expiry group on the date, contracts), oi_delta_5d / oi_delta_20d (change vs 5/20 trading SESSIONS earlier — session offsets index the underlying''s option calendar; NULL when the group had no row at the offset session, e.g. not yet listed), oi_max_20d (max oi_total over the trailing 20 sessions incl. the date; oi_total = oi_max_20d marks a 20-session OI high). CALL and PUT rows hold the SAME value (stats pool calls + puts). No FK to options_expiry_identity (open groups'' real expiry keys are not in the collapsed identity). Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_oi_stats.date                    IS 'Trading date.';
COMMENT ON COLUMN analysis.options_oi_stats.option_type            IS 'Option type as its static.label_dim (''option_type'' domain) SMALLINT code: 0 = CALL, 1 = PUT. The CALL and PUT rows of a group hold the SAME value (group-level stats).';
COMMENT ON COLUMN analysis.options_oi_stats.underlying_code         IS 'Underlying code (unified index codes; SZSE ETF options mapped via ETF->Index).';
COMMENT ON COLUMN analysis.options_oi_stats.expiry_date             IS 'REAL contract expiry date (no open-group collapse). CFFEX and SZSE expiry conventions differ within a month.';
COMMENT ON COLUMN analysis.options_oi_stats.oi_total                IS 'Total open interest of the expiry group on the date, contracts (sum over ALL active calls + puts). Feeds the per-expiry line-width encoding and the tooltip''s OI figure.';
COMMENT ON COLUMN analysis.options_oi_stats.oi_delta_5d             IS 'oi_total minus oi_total 5 trading sessions earlier (contracts; + = positions added, − = closed/rolled away). NULL when the group has no row at the offset session.';
COMMENT ON COLUMN analysis.options_oi_stats.oi_delta_20d            IS 'oi_total minus oi_total 20 trading sessions earlier (contracts). NULL when the group has no row at the offset session.';
COMMENT ON COLUMN analysis.options_oi_stats.oi_max_20d              IS 'Max oi_total over the trailing 20 sessions incl. the date (contracts). oi_total == oi_max_20d ⇒ the group sits at a 20-session OI high.';
