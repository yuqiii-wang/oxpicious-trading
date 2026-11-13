

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
--                        snapshot_date = report_date, code = sec_info.code, bare)
--      industry_portfolio / bond_type_portfolio / top10_bonds /
--        remaining_maturity → detail tables (loaded by build scripts)
--    The has_* flags tell consumers whether to look those tables up.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each SZSE-listed ETF's quarterly report registry — asset-allocation mix and share counts per quarter.
-- tags: fund reports, 基金报告, etf, asset allocation, 资产配置

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
COMMENT ON COLUMN stats.sec_reports.code                     IS 'Report/folder code, BARE 6-digit (e.g. 150009). FK → sec_info.code. JOINs sec_composition directly on code.';
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
COMMENT ON COLUMN stats.sec_reports.has_top10_holdings       IS 'TRUE when identify.csv reports 前十名股票投资明细 "有内容". Detail rows load to stats.sec_composition (source_type=''etf'', snapshot_date=report_date, code=sec_info.code, bare).';
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
