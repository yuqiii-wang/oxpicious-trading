
-- ----------------------------------------------------------------------------
-- Table: index_valuation
--   ← PE ratio + constituent count (unique to indexes)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each index's daily price-earnings ratio and constituent count.
-- tags: index, 指数, valuation, 估值, pe, 市盈率

CREATE TABLE IF NOT EXISTS stats.index_valuation (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    pe                        NUMERIC(10,4),
    cons_number               NUMERIC(10,0),

    CONSTRAINT pk_index_valuation PRIMARY KEY (code, date),
    CONSTRAINT fk_index_valuation_date_code FOREIGN KEY (code, date) REFERENCES stats.index_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'index_valuation', 8);

COMMENT ON TABLE  stats.index_valuation                    IS 'Index valuation metrics: PE ratio + constituent count.';
COMMENT ON COLUMN stats.index_valuation.pe                IS 'Price-to-earnings ratio (PE).';
COMMENT ON COLUMN stats.index_valuation.cons_number       IS 'Number of constituent stocks in the index.';

CREATE INDEX IF NOT EXISTS idx_index_valuation_date
    ON stats.index_valuation (date);
