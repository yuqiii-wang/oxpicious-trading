-- ============================================================================
--  Source-data audit — per-security coverage of the raw source datasets,
--  one row per (sec_type, code): how many days of source data the security
--  has (n_days) and the first / last date seen.
--
--  MIGRATED out of the security classification cache (the former
--  database/sec_statics/sec_classification.json, now the sec_classification/
--  shard tree): its per-index n_days / first_date / last_date entries were
--  build-time DATA parked in a hand-editable STATIC cache — they churned
--  every shard on every build and went stale for cache-only indices. The
--  classification build now upserts the same coverage here fresh from the
--  identity tables (index → stats.index_identity, stock →
--  stats.stock_identity) and the ETF OHLCV build adds the etf rows; the
--  shard tree holds static classification only (name / sector / industry /
--  tags / flags).
--
--  stats.sec_classification keeps its DENORMALIZED copies of the three
--  columns (live-data + stock + nav services read them there); this table
--  is the audit view of the same numbers for the monitoring UI — filter by
--  sec_type, search by code, spot securities with thin or stale coverage.
--
--  PK (sec_type, code) doubles as the search/filter index for the UI's
--  Source Data Audit table (GET /api/monitor/src-data-audit); the
--  standalone sec_type index gives the per-type totals and type-filtered
--  scans an index-only path.
--
--  Type columns are SMALLINT label codes (the label-code contract,
--  stats.label_dim + _common/labels.py + data_viz/api/services/
--  label-codes.ts):
--    sec_type — domain 'sec_type' (the analysis.mov_ave family contract,
--               REUSED here): 0 etf | 1 index | 2 stock
--
--  Current-state upsert, not append-only: one row per security, refreshed
--  by each build. Row count is bounded by the universe (~20k), so no
--  retention purge. Code conventions match sec_classification: index / etf
--  codes are bare 6-digit, stock codes carry the .SZ/.SS exchange suffix;
--  synthetic DUMMY_* indices are skipped (no source data of their own).
--
--  Applied after 06_db_health.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.src_data_audit (
    sec_type    SMALLINT    NOT NULL CHECK (sec_type IN (0, 1, 2)),  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code        TEXT        NOT NULL,
    exchange    TEXT,
    board       SMALLINT,   -- label code (stats.label_dim 'board'): 0=MAIN 1=STAR 2=GEM 3=BSE; stock rows only, NULL otherwise
    n_days      INTEGER     NOT NULL DEFAULT 0,
    first_date  DATE,
    last_date   DATE,
    updated_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_src_data_audit PRIMARY KEY (sec_type, code)
);

COMMENT ON TABLE  monitor.src_data_audit             IS 'Per-security source-data coverage audit: n_days / first_date / last_date per (sec_type, code). Migrated out of the security classification JSON cache (build-time data never belonged in the hand-editable static shards). Written by builds.classification (index from stats.index_identity, stock from stats.stock_identity) and builds.etf (etf rows from its OHLCV universe); the monitoring UI reads it via GET /api/monitor/src-data-audit.';
COMMENT ON COLUMN monitor.src_data_audit.sec_type    IS 'Security type the coverage belongs to — SMALLINT label code, domain ''sec_type'' (stats.label_dim; the analysis.mov_ave family contract, reused): 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity). PK component — the UI''s type filter hits this.';
COMMENT ON COLUMN monitor.src_data_audit.code        IS 'Security code, same convention as stats.sec_classification: index/etf bare 6-digit (000300 / 510050), stock WITH exchange suffix (000001.SZ). Synthetic DUMMY_* indices are never written. PK component — the UI''s code search hits this index.';
COMMENT ON COLUMN monitor.src_data_audit.exchange    IS 'Listing venue when one applies — TEXT (the deliberate label-code exception, same values as stats.sec_classification.exchange: SS / SZ / GEM / STAR / BJ / HK / OVERSEAS). index → derived from code prefix + name/sector (never stored on index_identity); stock → stats.stock_identity.exchange (code suffix); etf → stats.sec_classification.exchange. NULL = no venue applies (OTC feeder funds).';
COMMENT ON COLUMN monitor.src_data_audit.board       IS 'Listing board — SMALLINT label code, domain ''board'' (stats.label_dim): 0 = MAIN, 1 = STAR, 2 = GEM, 3 = BSE. Stock rows only, carried from stats.stock_identity.board; NULL for index/etf rows (the board concept does not apply).';
COMMENT ON COLUMN monitor.src_data_audit.n_days      IS 'Days of source data: COUNT(*) of the security''s identity-table rows (index/stock) or OHLCV-universe days (etf, same value as stats.sec_classification.n_days).';
COMMENT ON COLUMN monitor.src_data_audit.first_date  IS 'Earliest source-data date (MIN of identity / OHLCV dates). NULL = no source data.';
COMMENT ON COLUMN monitor.src_data_audit.last_date   IS 'Latest source-data date (MAX of identity / OHLCV dates) — a last_date far behind today flags stale / dead data for the security. NULL = no source data.';
COMMENT ON COLUMN monitor.src_data_audit.updated_at  IS 'When the build last refreshed this row (upsert timestamp, classification / ETF build cadence).';

-- Index-only path for the per-type totals (GROUP BY sec_type) and
-- type-filtered scans that don't need the code payload.
CREATE INDEX IF NOT EXISTS idx_src_data_audit_sec_type
    ON monitor.src_data_audit (sec_type);
