-- ============================================================================
--  Macro Daily - Split Tables
--  Sources: SGE 上海黄金交易所 daily spot bars (downloads/macro/gold_silver,
--           POST www.sge.com.cn/graph/Dailyhq);
--           COMEX 外盘 main-continuous daily klines (same downloader, sina
--           GlobalFuturesService.getGlobalFuturesDailyKLine).
--  Split into: macro_identity, macro_basic_stats
--  Reconstruct via: v_macro_daily view (see 99_reconstruct_views.sql)
--
--  Schema mirrors the futures family for symmetry:
--    macro_identity       ~  futures_identity
--    macro_basic_stats    ~  futures_basic_stats
--
--  Instruments (metal_type 0 = gold 黄金, 1 = silver 白银):
--    Au99.99 — SGE gold 99.99 spot (人民币金价, CNY/克)
--    Ag99.99 — SGE silver 99.99 spot (人民币银价, CNY/kg)
--    GC      — COMEX gold main continuous (美元金价, USD/ozt)
--    SI      — COMEX silver main continuous (美元银价, USD/ozt)
--
--  Code formats:
--    SGE:  <SPECIE><purity> e.g. "Au99.99", "Ag99.99" (dot codes, enumerated
--          in the CHECK first alternation — extend it for new SGE species)
--    International: <UPPERCASE SYMBOL> e.g. "GC", "SI", generic
--          ^[A-Z][A-Z0-9]{1,11}$ so future macro series (DXY, CL, ...) need
--          no ALTER.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: macro_identity
--   Identity core (PK) for all macro sub-tables
--   Mirrors futures_identity (code, date) with the macro instrument
--   classification (metal_type) + quote unit / source bookkeeping
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Which macro instrument (precious metal series) quoted on which day, with its metal class and quote unit — the shared identity for all macro daily tables.
-- tags: macro, 宏观, gold, 黄金, silver, 白银, precious metal, 贵金属, identity, 标识, reference, 参考

CREATE TABLE IF NOT EXISTS stats.macro_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    name                      TEXT          NOT NULL,
    metal_type                SMALLINT      NOT NULL,  -- label code (static.label_dim 'macro_metal'): 0=gold 1=silver
    unit                      TEXT          NOT NULL,
    source                    TEXT          NOT NULL,

    CONSTRAINT pk_macro_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_macro_identity_code_format
        CHECK (code ~ '^(Au99\.99|Ag99\.99)$'
               OR code ~ '^[A-Z][A-Z0-9]{1,11}$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'macro_identity', 8);

COMMENT ON TABLE  stats.macro_identity         IS 'Macro instrument identity: one row per (date, instrument_code) for the macro daily family (precious metals first). Sources: SGE daily spot bars (downloads/macro/gold_silver → www.sge.com.cn/graph/Dailyhq), COMEX main-continuous daily klines (sina GlobalFuturesService).';
COMMENT ON COLUMN stats.macro_identity.code        IS 'Macro instrument code: SGE spot species "Au99.99" (gold 99.99), "Ag99.99" (silver 99.99); international uppercase symbols "GC" (COMEX gold main continuous), "SI" (COMEX silver main continuous).';
COMMENT ON COLUMN stats.macro_identity.name        IS 'Instrument display name, e.g. "上海金99.99", "上海银99.99", "COMEX黄金主力连续", "COMEX白银主力连续".';
COMMENT ON COLUMN stats.macro_identity.metal_type  IS 'Metal class as its static.label_dim (''macro_metal'' domain) SMALLINT code: 0 = gold (黄金), 1 = silver (白银).';
COMMENT ON COLUMN stats.macro_identity.unit        IS 'Quote unit as published by the source, no conversion: "CNY/克" (SGE gold), "CNY/kg" (SGE silver), "USD/ozt" (COMEX gold/silver).';
COMMENT ON COLUMN stats.macro_identity.source      IS 'Data source slug: "sge" (www.sge.com.cn) or "sina" (stock2.finance.sina.com.cn 外盘期货 klines).';


-- ----------------------------------------------------------------------------
-- Indexes for macro_identity
--   (code, date) PK covers code-first lookups — keep a date-first index.
-- ----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_macro_identity_date
    ON stats.macro_identity (date);

CREATE INDEX IF NOT EXISTS idx_macro_identity_type_date
    ON stats.macro_identity (metal_type, date);
