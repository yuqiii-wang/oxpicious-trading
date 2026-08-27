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
--  sec_owners source: _common/sec_statics/sec_owners.json (curated, hand-editable
--  cache).  stats.sec_classification.owner_id is a LOGICAL (non-FK) reference to
--  sec_owners.owner_id — matched in-memory by builds.classification (load_owners
--  + build_owner_matchers + match_etf_owner) so no DB join is required at
--  classification time.  The table is populated here (truncate + rebuild each run).
--
--  Code convention:
--    `code` is the BARE 6-digit report/folder code (e.g. 150009, 159001) —
--    these are SZSE-listed funds only (159xxx / 150xxx / 16xxxx). To JOIN to
--    stats.sec_classification or stats.sec_composition (which store ETF codes
--    WITH exchange suffix), append '.SZ':  sec_info.code || '.SZ'.
--    `fund_main_code` captures 基金主代码 when it differs from the folder code
--    (e.g. structured/分级 funds: folder 150009 → main code 161207).
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: sec_info
--    Static fund attributes from identify.csv. One row per fund code.
--    Populated with the LATEST observed values; last_report_date records the
--    report quarter the snapshot was taken from.
-- ----------------------------------------------------------------------------
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
COMMENT ON COLUMN stats.sec_info.code                         IS 'Report/folder code, BARE 6-digit (e.g. 150009, 159001). SZSE-listed funds only. JOIN to stats.sec_classification / stats.sec_composition via code || ''.SZ''.';
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


-- ----------------------------------------------------------------------------
--  Table: sec_reports
--    One row per (code, report quarter). Holds:
--      · report header      — period, total shares (from identify.csv)
--      · asset-allocation   — MIX columns from asset_portfolio.csv
--                             (equity / fixed income / precious metal /
--                              derivatives / reverse repo / bank deposit /
--                              other / total; each _amt + _pct)
--      · content flags      — which detail sections have content
--                             (pointers to detail tables / sec_composition)
--      · section row counts — from identify.csv "有内容(N行)" markers
--
--    Detail row data is NOT stored here — it loads to other tables:
--      top10_holdings → stats.sec_composition (source_type=''etf'',
--                        snapshot_date = report_date, code = sec_info.code||''.SZ'')
--      industry_portfolio / bond_type_portfolio / top10_bonds /
--        remaining_maturity → detail tables (loaded by build scripts)
--    The has_* flags tell consumers whether to look those tables up.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_reports (
    code                       TEXT          NOT NULL,
    report_period              TEXT          NOT NULL,
    report_year                SMALLINT      NOT NULL,
    report_quarter             SMALLINT      NOT NULL
        CHECK (report_quarter IN (1,2,3,4)),
    report_date                DATE          NOT NULL,

    -- 报告期末基金份额总额 (total shares outstanding at period end)
    total_shares               NUMERIC(24,4),
    total_shares_text          TEXT,

    -- Asset-allocation MIX (from asset_portfolio.csv)
    -- 金额(元) + 占基金总资产的比例(%). NULL when source cell is "-".
    equity_amt                 NUMERIC(24,4),
    equity_pct                 NUMERIC(10,4),
    fixed_income_amt           NUMERIC(24,4),
    fixed_income_pct           NUMERIC(10,4),
    precious_metal_amt         NUMERIC(24,4),
    precious_metal_pct         NUMERIC(10,4),
    derivatives_amt            NUMERIC(24,4),
    derivatives_pct            NUMERIC(10,4),
    reverse_repo_amt           NUMERIC(24,4),
    reverse_repo_pct           NUMERIC(10,4),
    bank_deposit_amt           NUMERIC(24,4),
    bank_deposit_pct           NUMERIC(10,4),
    other_assets_amt           NUMERIC(24,4),
    other_assets_pct           NUMERIC(10,4),
    total_assets_amt           NUMERIC(24,4),
    total_assets_pct           NUMERIC(10,4),

    -- Section content flags + row counts (from identify.csv markers)
    -- has_* = TRUE when identify.csv reports "有内容(N行)"; FALSE when "无内容".
    has_asset_portfolio        BOOLEAN       NOT NULL DEFAULT FALSE,
    has_industry_portfolio     BOOLEAN       NOT NULL DEFAULT FALSE,
    has_top10_holdings         BOOLEAN       NOT NULL DEFAULT FALSE,
    has_bond_type_portfolio    BOOLEAN       NOT NULL DEFAULT FALSE,
    has_top10_bonds            BOOLEAN       NOT NULL DEFAULT FALSE,
    has_remaining_maturity     BOOLEAN       NOT NULL DEFAULT FALSE,

    n_asset_portfolio_rows     SMALLINT,
    n_industry_portfolio_rows  SMALLINT,
    n_top10_holdings_rows      SMALLINT,
    n_bond_type_portfolio_rows SMALLINT,
    n_top10_bonds_rows         SMALLINT,
    n_remaining_maturity_rows  SMALLINT,

    CONSTRAINT pk_sec_reports PRIMARY KEY (code, report_date),
    CONSTRAINT fk_sec_reports_code FOREIGN KEY (code) REFERENCES stats.sec_info(code),
    CONSTRAINT chk_sec_reports_code_format CHECK (code ~ '^\d{6}$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_reports', 8);

COMMENT ON TABLE  stats.sec_reports                          IS 'Per-quarter SZSE ETF report registry. One row per (code, quarter-end). Holds the report header (period, total shares), the asset-allocation MIX (equity/fixed income/cash/derivatives/etc.), and content flags pointing to detail tables. Source: szse_etf_reports identify.csv + asset_portfolio.csv.';
COMMENT ON COLUMN stats.sec_reports.code                     IS 'Report/folder code, BARE 6-digit (e.g. 150009). FK → sec_info.code. JOIN to sec_composition via code || ''.SZ''.';
COMMENT ON COLUMN stats.sec_reports.report_period            IS '报告期 original text, e.g. "2020年第1季度". Paired with report_year/report_quarter/report_date for sorting.';
COMMENT ON COLUMN stats.sec_reports.report_year              IS 'Calendar year of the report period (e.g. 2020).';
COMMENT ON COLUMN stats.sec_reports.report_quarter           IS 'Quarter number 1-4. CHECK-constrained.';
COMMENT ON COLUMN stats.sec_reports.report_date              IS 'Quarter-end DATE derived from report_period (Q1→03-31, Q2→06-30, Q3→09-30, Q4→12-31). PK component. Also the snapshot_date used when loading top10_holdings into sec_composition.';
COMMENT ON COLUMN stats.sec_reports.total_shares             IS '报告期末基金份额总额 (total fund shares at period end), parsed numeric. NULL when missing.';
COMMENT ON COLUMN stats.sec_reports.total_shares_text        IS 'Original shares text including 单位 (e.g. "189,089,089.46份") for audit.';
COMMENT ON COLUMN stats.sec_reports.equity_amt               IS '权益投资 amount (yuan) — stock holdings. NULL when source "-". From asset_portfolio.csv.';
COMMENT ON COLUMN stats.sec_reports.equity_pct               IS '权益投资 % of total fund assets. From asset_portfolio.csv.';
COMMENT ON COLUMN stats.sec_reports.fixed_income_amt         IS '固定收益投资 amount (yuan) — bond / fixed-income holdings. NULL when "-".';
COMMENT ON COLUMN stats.sec_reports.fixed_income_pct         IS '固定收益投资 % of total fund assets.';
COMMENT ON COLUMN stats.sec_reports.precious_metal_amt       IS '贵金属投资 amount (yuan). NULL when "-".';
COMMENT ON COLUMN stats.sec_reports.precious_metal_pct       IS '贵金属投资 % of total fund assets.';
COMMENT ON COLUMN stats.sec_reports.derivatives_amt          IS '金融衍生品投资 amount (yuan). NULL when "-".';
COMMENT ON COLUMN stats.sec_reports.derivatives_pct          IS '金融衍生品投资 % of total fund assets.';
COMMENT ON COLUMN stats.sec_reports.reverse_repo_amt         IS '买入返售金融资产 amount (yuan) — reverse repo. NULL when "-".';
COMMENT ON COLUMN stats.sec_reports.reverse_repo_pct         IS '买入返售金融资产 % of total fund assets.';
COMMENT ON COLUMN stats.sec_reports.bank_deposit_amt         IS '银行存款和结算备付金合计 amount (yuan) — cash & settlement reserves. NULL when "-".';
COMMENT ON COLUMN stats.sec_reports.bank_deposit_pct         IS '银行存款和结算备付金合计 % of total fund assets.';
COMMENT ON COLUMN stats.sec_reports.other_assets_amt         IS '其他各项资产 / 其他资产 amount (yuan). NULL when "-".';
COMMENT ON COLUMN stats.sec_reports.other_assets_pct         IS '其他各项资产 / 其他资产 % of total fund assets.';
COMMENT ON COLUMN stats.sec_reports.total_assets_amt         IS '合计 amount (yuan) — total fund assets.';
COMMENT ON COLUMN stats.sec_reports.total_assets_pct         IS '合计 % of total fund assets (normally 100.00).';
COMMENT ON COLUMN stats.sec_reports.has_asset_portfolio      IS 'TRUE when identify.csv reports 投资组合报告-报告期末基金资产组合情况 "有内容". The MIX columns above are populated only when this is TRUE.';
COMMENT ON COLUMN stats.sec_reports.has_industry_portfolio   IS 'TRUE when identify.csv reports 按行业分类的境内股票投资组合 "有内容". Detail rows load to a separate industry-portfolio table.';
COMMENT ON COLUMN stats.sec_reports.has_top10_holdings       IS 'TRUE when identify.csv reports 前十名股票投资明细 "有内容". Detail rows load to stats.sec_composition (source_type=''etf'', snapshot_date=report_date, code=sec_info.code||''.SZ'').';
COMMENT ON COLUMN stats.sec_reports.has_bond_type_portfolio  IS 'TRUE when identify.csv reports 按债券品种分类的债券投资组合 "有内容". Detail rows load to a separate bond-type table.';
COMMENT ON COLUMN stats.sec_reports.has_top10_bonds          IS 'TRUE when identify.csv reports 前十名债券投资明细 "有内容". Detail rows load to a separate bond-holdings table.';
COMMENT ON COLUMN stats.sec_reports.has_remaining_maturity   IS 'TRUE when identify.csv reports 投资组合平均剩余期限分布比例 "有内容" (money-market / bond funds). Detail rows load to a separate remaining-maturity table.';
COMMENT ON COLUMN stats.sec_reports.n_asset_portfolio_rows   IS 'Row count from identify.csv "有内容(N行)" for the asset-portfolio section. NULL when section absent.';
COMMENT ON COLUMN stats.sec_reports.n_industry_portfolio_rows IS 'Row count for the industry stock-portfolio section. NULL when section absent.';
COMMENT ON COLUMN stats.sec_reports.n_top10_holdings_rows    IS 'Row count for the top-10 stock holdings section. NULL when section absent.';
COMMENT ON COLUMN stats.sec_reports.n_bond_type_portfolio_rows IS 'Row count for the bond-type portfolio section. NULL when section absent.';
COMMENT ON COLUMN stats.sec_reports.n_top10_bonds_rows       IS 'Row count for the top-10 bond holdings section. NULL when section absent.';
COMMENT ON COLUMN stats.sec_reports.n_remaining_maturity_rows IS 'Row count for the remaining-maturity distribution section. NULL when section absent.';

CREATE INDEX IF NOT EXISTS idx_sec_reports_report_date
    ON stats.sec_reports (report_date);

CREATE INDEX IF NOT EXISTS idx_sec_reports_code_period
    ON stats.sec_reports (code, report_year, report_quarter);

CREATE INDEX IF NOT EXISTS idx_sec_reports_has_top10_holdings
    ON stats.sec_reports (report_date)
    WHERE has_top10_holdings = TRUE;

CREATE INDEX IF NOT EXISTS idx_sec_reports_has_top10_bonds
    ON stats.sec_reports (report_date)
    WHERE has_top10_bonds = TRUE;


-- ----------------------------------------------------------------------------
--  Sec Owners — registry of security owners
--  For ETFs the owner is the fund manager / issuer (e.g. 南方, 华夏, 国泰).
--  For stocks the owner is the listed company itself.  Indices have no owner.
--
--  MIGRATED from 07_sec_classification.sql.  Now populated by builds.sec_info
--  (truncate + rebuild each run from sec_owners.json).
--
--  Source: _common/sec_statics/sec_owners.json (curated, hand-editable cache
--  maintained alongside builds.sec_info).  Each entry carries one or more
--  `aliases` (short-name prefixes used to match the ETF name) and `full_names`
--  (legal entity names that match the CSV `管理人` column exactly).
--
--  stats.sec_classification.owner_id is a logical (non-FK) reference to this
--  table's `owner_id` column — populated in-memory by builds.classification
--  (load_owners + build_owner_matchers + match_etf_owner).  No DB join is
--  required at classification time; this table exists for UI/reference joins.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_owners (
    owner_id                  TEXT          NOT NULL,
    name                      TEXT          NOT NULL,
    type                      TEXT,
    aliases                   TEXT[]        NOT NULL DEFAULT '{}',
    full_names                TEXT[]        NOT NULL DEFAULT '{}',

    CONSTRAINT pk_sec_owners PRIMARY KEY (owner_id),
    CONSTRAINT chk_sec_owners_type CHECK (type IN
        ('fund_manager', 'broker', 'asset_manager', 'insurance', 'bank', 'company', 'other'))
);

COMMENT ON TABLE  stats.sec_owners               IS 'Registry of security owners (ETF fund managers / issuers, stock companies). Migrated from 07_sec_classification.sql; populated by builds.sec_info (truncate + rebuild from _common/sec_statics/sec_owners.json). Referenced logically (non-FK) by stats.sec_classification.owner_id.';
COMMENT ON COLUMN stats.sec_owners.owner_id      IS 'Stable identifier (slug). Used as the logical FK target by sec_classification.owner_id.';
COMMENT ON COLUMN stats.sec_owners.name          IS 'Display name (short form), e.g. 南方, 华夏, 国泰君安.';
COMMENT ON COLUMN stats.sec_owners.type          IS 'Owner type: fund_manager, broker, asset_manager, insurance, bank, company, other.';
COMMENT ON COLUMN stats.sec_owners.aliases       IS 'Short-name prefixes used to match ETF names (longest alias wins). e.g. ["南方"] matches "南方中证全指食品交易型...". Includes the name itself by convention.';
COMMENT ON COLUMN stats.sec_owners.full_names    IS 'Full legal entity names that match the etf_index_map CSV `管理人` column exactly, e.g. ["南方基金管理股份有限公司"].';

-- Add type / aliases / full_names columns to existing tables (no-op if already
-- present). Needed because CREATE TABLE IF NOT EXISTS does not add new columns
-- to an existing table — the columns were added to the DDL after the table was
-- first created, so the live table lacks them until these ALTERs run.
ALTER TABLE stats.sec_owners ADD COLUMN IF NOT EXISTS type       TEXT;
ALTER TABLE stats.sec_owners ADD COLUMN IF NOT EXISTS aliases    TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE stats.sec_owners ADD COLUMN IF NOT EXISTS full_names TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_sec_owners_type
    ON stats.sec_owners (type)
    WHERE type IS NOT NULL;
