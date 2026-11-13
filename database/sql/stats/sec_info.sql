-- ============================================================================
--  Sec Info + Sec Reports + Sec Owners — SZSE ETF quarterly report registry
--  and the curated security-owner registry.
--
--  Three tables:
--    sec_info    — one row per fund code, static (time-invariant) attributes
--                  pulled from the LATEST identify.csv. Latest-value snapshot
--                  (manager / custodian / benchmark can change rarely across
--                  a fund's life; last_report_date records freshness).
--    sec_reports — one row per (code, report quarter). Holds the per-quarter
--                  report header (period, total shares), the asset-allocation
--                  MIX (direct columns), and content flags + row counts that
--                  point to the detail tables below.
--    sec_owners  — registry of security owners (ETF fund managers / issuers,
--                  stock companies). Migrated here from 07_sec_classification.sql;
--                  now populated by builds.sec_info (not build_classification).
--
--  Source: temps/szse_etf_reports/<code>/<code>_<YYYYQn>_*.csv
--    · identify.csv            → sec_info (static fund attributes)
--                                + sec_reports header (report period, shares,
--                                section content flags)
--    · asset_portfolio.csv     → sec_reports asset-allocation MIX columns
--                                (equity / fixed income / precious metal /
--                                derivatives / reverse repo / bank deposit /
--                                other / total) — captures the fund's blend of
--                                stocks, bonds, cash/FX, etc.
--    · top10_holdings.csv      → stats.sec_composition (source_type='etf',
--                                snapshot_date = report quarter-end). Referenced
--                                from sec_reports via has_top10_holdings flag.
--    · industry_portfolio.csv  → detail table (loaded elsewhere); referenced
--                                via has_industry_portfolio flag.
--    · bond_type_portfolio.csv → detail table (loaded elsewhere); referenced
--                                via has_bond_type_portfolio flag.
--    · top10_bonds.csv         → detail table (loaded elsewhere); referenced
--                                via has_top10_bonds flag.
--    · remaining_maturity.csv  → detail table (loaded elsewhere); referenced
--                                via has_remaining_maturity flag.
--
--  sec_owners source: database/sec_statics/sec_owners.json (curated, hand-editable
--  cache).  stats.sec_classification.owner_id is a LOGICAL (non-FK) reference to
--  sec_owners.owner_id — matched in-memory by builds.classification (load_owners
--  + build_owner_matchers + match_etf_owner) so no DB join is required at
--  classification time.  The table is populated here (truncate + rebuild each run).
--
--  Code convention:
--    `code` is the BARE 6-digit report/folder code (e.g. 150009, 159001) —
--    these are SZSE-listed funds only (159xxx / 150xxx / 16xxxx). ETF codes
--    in stats.sec_classification / stats.sec_composition are BARE too (since
--    2026-09-30), so they JOIN directly:  sec_info.code = sec_composition.code
--    (the former append-'.SZ' surgery is retired).
--    `fund_main_code` captures 基金主代码 when it differs from the folder code
--    (e.g. structured/分级 funds: folder 150009 → main code 161207).
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: sec_info
--    Static fund attributes from identify.csv. One row per fund code.
--    Populated with the LATEST observed values; last_report_date records the
--    report quarter the snapshot was taken from.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The static fund attributes of SZSE-listed ETFs.
-- tags: fund attributes, 基金属性, etf, reference, 参考

CREATE TABLE IF NOT EXISTS stats.sec_info (
    code                       TEXT          NOT NULL,
    fund_main_code             TEXT,
    name                       TEXT          NOT NULL DEFAULT '',
    exchange_abbreviation      TEXT,
    operation_method           TEXT,
    contract_effective_date    DATE,
    benchmark                  TEXT,
    risk_return_characteristics TEXT,
    manager                    TEXT,
    custodian                  TEXT,
    last_report_date           DATE,

    CONSTRAINT pk_sec_info PRIMARY KEY (code),
    CONSTRAINT chk_sec_info_code_format CHECK (code ~ '^\d{6}$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_info', 8);

COMMENT ON TABLE  stats.sec_info                              IS 'Static fund attributes for SZSE-listed ETFs, loaded from szse_etf_reports identify.csv. One row per fund code (latest-value snapshot). Source: temps/szse_etf_reports/<code>/<code>_<YYYYQn>_identify.csv.';
COMMENT ON COLUMN stats.sec_info.code                         IS 'Report/folder code, BARE 6-digit (e.g. 150009, 159001). SZSE-listed funds only. JOINs stats.sec_classification / stats.sec_composition directly on code (ETF codes are bare there since 2026-09-30).';
COMMENT ON COLUMN stats.sec_info.fund_main_code               IS '基金主代码 from identify.csv. Usually equals `code` but differs for structured/分级 funds (e.g. folder 150009 → main code 161207). NULL when same as code.';
COMMENT ON COLUMN stats.sec_info.name                         IS '基金简称 (fund short name).';
COMMENT ON COLUMN stats.sec_info.exchange_abbreviation        IS '场内简称 (exchange trading abbreviation). NULL when the report omits it (common for plain equity ETFs; present for money-market / 分级 funds).';
COMMENT ON COLUMN stats.sec_info.operation_method             IS '基金运作方式, e.g. 契约型开放式, 交易型开放式.';
COMMENT ON COLUMN stats.sec_info.contract_effective_date      IS '基金合同生效日 (fund contract effective date). Parsed from Chinese date text (e.g. "2009年10 月14日").';
COMMENT ON COLUMN stats.sec_info.benchmark                    IS '业绩比较基准 (performance benchmark), e.g. 95%×沪深300指数收益率＋5%×银行同业存款利率.';
COMMENT ON COLUMN stats.sec_info.risk_return_characteristics  IS '风险收益特征 (risk-return profile narrative).';
COMMENT ON COLUMN stats.sec_info.manager                      IS '基金管理人 (fund manager / issuer legal name).';
COMMENT ON COLUMN stats.sec_info.custodian                    IS '基金托管人 (fund custodian bank legal name).';
COMMENT ON COLUMN stats.sec_info.last_report_date             IS 'Quarter-end DATE of the LATEST identify.csv this row was refreshed from. Records snapshot freshness (manager / benchmark / name can change rarely across a fund''s life).';

CREATE INDEX IF NOT EXISTS idx_sec_info_manager
    ON stats.sec_info (manager)
    WHERE manager IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sec_info_fund_main_code
    ON stats.sec_info (fund_main_code)
    WHERE fund_main_code IS NOT NULL;
