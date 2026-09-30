-- ============================================================================
--  Security Composition — ALL constituent holdings per ETF or INDEX per
--  composition snapshot.
--
--  Source: build_szse_sse_etf_and_margin.py
--    · Full composition (comp_long): ALL holdings for ETFs, rank 1..N
--    · Index composition (CSI closeweight): ALL constituents for CSI indices
--
--  source_type distinguishes the data origin:
--    'etf'   — ETF holdings (code = bare ETF code, e.g. 159001)
--    'index' — Index composition (code = bare index code, e.g. 930606)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: sec_composition
--   One row per (code, snapshot_date, rank).
--   - For ETFs (source_type='etf'): code = "159001" (bare 6-digit ETF code)
--   - For indices (source_type='index'): code = "930606" (bare 6-digit index code)
--   stock_code = constituent code WITH exchange suffix ("000001.SZ"), matching
--   the convention of stock_identity / stock_basic_stats (suffix required so
--   joins against those tables need no stripping).
--   rank = 1 is the largest weight; rank can exceed 5 for full composition data.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_composition (
    snapshot_date             DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    source_type               TEXT          NOT NULL DEFAULT 'etf',
    rank                      SMALLINT      NOT NULL,
    stock_code                TEXT,
    stock_name                TEXT,
    weight_pct                NUMERIC(10,6) NOT NULL DEFAULT 0,

    CONSTRAINT pk_sec_composition PRIMARY KEY (code, snapshot_date, rank),
    CONSTRAINT chk_sec_composition_code
        CHECK (code ~ '^(\d{6}|H\d{5})$'),
    CONSTRAINT chk_sec_composition_source
        CHECK (source_type IN ('etf', 'index'))
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_composition', 8);


COMMENT ON TABLE  stats.sec_composition                IS 'ALL constituent holdings per ETF or INDEX per composition snapshot. Native HASH partitioned by code.';
COMMENT ON COLUMN stats.sec_composition.snapshot_date   IS 'Composition snapshot date (one snapshot per ETF/index, applied forward via merge_asof).';
COMMENT ON COLUMN stats.sec_composition.code            IS 'ETF code (bare, e.g. 159001) or index code (e.g. 930606, H30007 for CSI cross-market).';
COMMENT ON COLUMN stats.sec_composition.source_type     IS 'Data origin: "etf" = ETF holdings, "index" = index composition (CSI closeweight).';
COMMENT ON COLUMN stats.sec_composition.rank            IS 'Holding rank (1 = largest weight). Rank > 5 for full composition data.';

CREATE INDEX IF NOT EXISTS idx_sec_composition_snapshot_code
    ON stats.sec_composition (snapshot_date, code);

CREATE INDEX IF NOT EXISTS idx_sec_composition_stock
    ON stats.sec_composition (stock_code)
    WHERE stock_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sec_composition_source
    ON stats.sec_composition (source_type);

-- ----------------------------------------------------------------------------
-- Data migration: stock_code bare -> suffixed.
-- Historical rows were written with bare 6-digit stock_code; the convention
-- is now suffixed ("000001.SZ") to match stock_identity/stock_basic_stats.
-- Suffix mapping by first digit (idempotent: only bare codes are touched):
--   5/6/9 -> .SS  (SSE stocks + funds + B-shares)
--   4/8   -> .BJ  (BSE)
--   else  -> .SZ  (SZSE stocks 0/2/3 + funds 1)
-- ----------------------------------------------------------------------------
UPDATE stats.sec_composition
   SET stock_code = stock_code || CASE LEFT(stock_code, 1)
                      WHEN '5' THEN '.SS'
                      WHEN '6' THEN '.SS'
                      WHEN '9' THEN '.SS'
                      WHEN '4' THEN '.BJ'
                      WHEN '8' THEN '.BJ'
                      ELSE '.SZ'
                   END
 WHERE stock_code ~ '^\d{6}$';

COMMENT ON COLUMN stats.sec_composition.stock_code
    IS 'Constituent code WITH exchange suffix (e.g. 000001.SZ), matching stock_identity/stock_basic_stats.';

-- ----------------------------------------------------------------------------
-- Idempotent migration (2026-09-30): strip the exchange suffix from ETF
-- codes — ETF codes are BARE 6-digit everywhere now (stock_code keeps its
-- suffix; index codes were already bare). Atomic DO block: collision
-- pre-check (a stripped ETF code colliding with an existing index code —
-- prefix families are disjoint by design, this is belt-and-braces) →
-- strip source_type='etf' rows (hash-partition row movement, one-time WAL
-- burst) → bare-only CHECK from any starting state.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    def        text;
    n_collide  bigint;
BEGIN
    SELECT count(*) INTO n_collide
      FROM stats.sec_composition s
     WHERE s.source_type = 'etf' AND s.code ~ '^\d{6}\.'
       AND EXISTS (SELECT 1 FROM stats.sec_composition b
                    WHERE b.code = split_part(s.code, '.', 1)
                      AND b.snapshot_date = s.snapshot_date
                      AND b.rank = s.rank);
    IF n_collide > 0 THEN
        RAISE EXCEPTION
            'stats.sec_composition: % ETF rows would collide with existing bare rows',
            n_collide;
    END IF;

    UPDATE stats.sec_composition
       SET code = split_part(code, '.', 1)
     WHERE source_type = 'etf' AND code ~ '^\d{6}\.';

    SELECT pg_get_constraintdef(oid) INTO def
      FROM pg_constraint
     WHERE conname = 'chk_sec_composition_code'
       AND conrelid = 'stats.sec_composition'::regclass
     LIMIT 1;
    -- Backslash-free probe: '{5})$' only occurs in the bare-only definition
    -- (LIKE '\d' is an escaped 'd', not a literal backslash-d).
    IF def IS NULL OR def NOT LIKE '%{5})$%' THEN
        ALTER TABLE stats.sec_composition
            DROP CONSTRAINT IF EXISTS chk_sec_composition_code;
        ALTER TABLE stats.sec_composition
            ADD CONSTRAINT chk_sec_composition_code
            CHECK (code ~ '^(\d{6}|H\d{5})$');
    END IF;
END $$;
