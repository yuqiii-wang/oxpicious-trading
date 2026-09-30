-- ============================================================================
--  Sec ETF Links — ETF feeder fund (联接基金) → target (master) ETF mapping.
--
--  Market design (mirrors the ETF → tracking-index hierarchy carried on
--  stats.sec_classification.parent_index_code, one level DOWN from ETFs):
--
--    OTC investor → FEEDER fund (联接基金, off-exchange) → TARGET ETF
--  (exchange-listed) → tracking index (sec_classification.parent_index_code)
--
--  A feeder fund (ETF Feeder Fund; the UCITS feeder/master analogue — CSRC
--  requires ≥90% of feeder NAV invested in ONE target ETF) bridges OTC
--  channels (banks / 天天基金 / 支付宝) into the exchange-traded product:
--  it subscribes/redeems target-ETF shares in the primary market at NAV,
--  never paying the ETF's market spread. Feeder and target are ALWAYS the
--  same fund manager tracking the same index — that invariant is how the
--  mapping is resolved from the CSIndex fund list (manager + index → the
--  manager's ETF on that index).
--
--  Feeders are NOT securities rows in stats.sec_classification (no listing,
--  no OHLCV, no margin) — this dedicated table holds the link universe,
--  keyed by the OTC feeder code. One row per feeder family: CSIndex lists
--  a single row per 联接基金 (the A share class); the A/C/Y/I share-class
--  codes are sold OTC and are not part of this mapping.
--
--  Source: downloads.etf.csindex.linked_etf (etf_feeder_map_<date>.csv,
--  fundType "联接基金" rows from POST /csindex-home/index-list/
--  funds-tracking-index with no fundType filter).
--  Loaded by: builds.classification (truncate + rebuild each run — the
--  table is a ~700-row snapshot registry, same pattern as sec_owners).
--
--  Consumed by: /api/sec-composition/linked-feeders?code=<etf> — the ETF
--  trend page's "Linked Feeder Funds" section (mirror of the Index page's
--  LinkedEtfsList, which walks the parent_index_code hierarchy UP).
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.sec_etf_links (
    feeder_code        TEXT          NOT NULL,
    feeder_name        TEXT          NOT NULL DEFAULT '',
    is_initiator       BOOLEAN       NOT NULL DEFAULT FALSE,
    target_etf_code    TEXT          NOT NULL,
    target_etf_name    TEXT          NOT NULL DEFAULT '',
    target_etf_type    TEXT          NOT NULL DEFAULT '',
    index_code         TEXT          NOT NULL DEFAULT '',
    index_name         TEXT          NOT NULL DEFAULT '',
    manager            TEXT          NOT NULL DEFAULT '',
    aum_yi             NUMERIC(12,4),
    inception_date     DATE,

    CONSTRAINT pk_sec_etf_links PRIMARY KEY (feeder_code)
);

COMMENT ON TABLE  stats.sec_etf_links             IS 'ETF feeder fund (联接基金) → target (master) ETF mapping. Feeder: off-exchange fund investing >=90% NAV in ONE listed target ETF (same manager + same index by market design); loaded from etf_feeder_map_*.csv by builds.classification (truncate + rebuild). Companion of the ETF→index hierarchy on sec_classification.parent_index_code — one level further from the index.';
COMMENT ON COLUMN stats.sec_etf_links.feeder_code     IS 'OTC feeder fund code, bare 6-digit (e.g. 012761 华泰柏瑞红利ETF联接A-family). One row per feeder family (CSIndex lists no A/C split).';
COMMENT ON COLUMN stats.sec_etf_links.feeder_name     IS 'Full feeder fund legal name (e.g. 华泰柏瑞上证红利交易型开放式指数证券投资基金联接基金).';
COMMENT ON COLUMN stats.sec_etf_links.is_initiator    IS 'TRUE when the feeder name carries 发起式 (initiator fund, seed money from the manager).';
COMMENT ON COLUMN stats.sec_etf_links.target_etf_code IS 'Target (master) ETF code, bare 6-digit (e.g. 510880) — joins sec_classification.code / v_etf_margin.code directly. Resolved as the manager''s unique ETF on the feeder''s tracked index.';
COMMENT ON COLUMN stats.sec_etf_links.target_etf_type IS 'CSIndex fundType of the target: ETF or 增强ETF (enhanced).';
COMMENT ON COLUMN stats.sec_etf_links.index_code      IS 'Tracked index code, bare (e.g. 000015) — equals the target ETF''s sec_classification.parent_index_code.';
COMMENT ON COLUMN stats.sec_etf_links.index_name      IS 'Tracked index Chinese name (e.g. 红利指数).';
COMMENT ON COLUMN stats.sec_etf_links.manager         IS 'Fund manager legal name (identical for feeder and target by market design).';
COMMENT ON COLUMN stats.sec_etf_links.aum_yi          IS 'Feeder net asset value in 亿元 from CSIndex (0.00 for young feeders).';
COMMENT ON COLUMN stats.sec_etf_links.inception_date  IS 'Feeder inception date.';

CREATE INDEX IF NOT EXISTS idx_sec_etf_links_target
    ON stats.sec_etf_links (target_etf_code);

-- Idempotent migration (2026-09-30): strip the exchange suffix from ETF
-- codes — ETF codes are BARE 6-digit everywhere now (feeder_code and
-- index_code were already bare).
UPDATE stats.sec_etf_links
   SET target_etf_code = split_part(target_etf_code, '.', 1)
 WHERE target_etf_code ~ '^\d{6}\.';
