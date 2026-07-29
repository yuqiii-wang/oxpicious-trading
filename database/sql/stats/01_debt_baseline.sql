-- ============================================================================
--  Debt Baseline - Split Tables
--  Original: debt_baseline table from schema.sql
--  Split into: debt_identity, debt_omo, debt_repo, debt_outright_repo,
--              debt_mlf, debt_shibor, debt_treasury
--  Reconstruct via: v_debt_baseline view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: debt_identity
--   Identity core (PK) for all debt_baseline sub-tables
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_identity (
    date                      DATE          NOT NULL,
    CONSTRAINT pk_debt_identity PRIMARY KEY (date)
);

COMMENT ON TABLE  stats.debt_identity                IS 'Debt baseline identity: one row per trading day. PK shared by all debt sub-tables.';

-- ----------------------------------------------------------------------------
-- Table: debt_omo
--   ← PBoC Open Market Operations (reverse repo)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_omo (
    date                      DATE          NOT NULL,
    omo_rate                  NUMERIC(8,4),
    omo_quantity              NUMERIC(18,4),
    omo_tenor_days            NUMERIC(6,1),
    omo_tenor_label           TEXT,
    omo_all_rates             TEXT,
    omo_all_tenors            TEXT,
    omo_all_quantities        TEXT,
    omo_dur_qty_pairs         TEXT,

    CONSTRAINT pk_debt_omo PRIMARY KEY (date),
    CONSTRAINT fk_debt_omo_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_omo                     IS 'PBoC Open Market Operations (reverse repo).';
COMMENT ON COLUMN stats.debt_omo.omo_all_rates       IS 'Comma-separated multi-tender rates (e.g. "2.2,2.4") on days when PBoC ran >1 reverse-repo tender.';

-- ----------------------------------------------------------------------------
-- Table: debt_repo
--   ← Reverse-repo lifecycle (running cumulative of all outstanding repos)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_repo (
    date                      DATE          NOT NULL,
    repo_start_quantity       NUMERIC(18,4) NOT NULL DEFAULT 0,
    repo_end_quantity         NUMERIC(18,4) NOT NULL DEFAULT 0,
    repo_net_injection        NUMERIC(18,4) NOT NULL DEFAULT 0,
    repo_cumulative           NUMERIC(18,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_debt_repo PRIMARY KEY (date),
    CONSTRAINT fk_debt_repo_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_repo                    IS 'Reverse-repo lifecycle: running cumulative of all outstanding repos.';
COMMENT ON COLUMN stats.debt_repo.repo_cumulative    IS 'Running outstanding reverse-repo balance (亿元) — peak level indicates PBoC net liquidity stance.';

-- ----------------------------------------------------------------------------
-- Table: debt_outright_repo
--   ← PBoC outright-repo tender (bond-buying; longer-tenor liquidity injection)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_outright_repo (
    date                      DATE          NOT NULL,
    outright_repo_marker      SMALLINT NOT NULL DEFAULT 0
        CHECK (outright_repo_marker IN (0,1)),
    outright_repo_quantity    NUMERIC(18,4),
    outright_repo_tenor_days  NUMERIC(6,1),
    outright_repo_tenor_label TEXT,
    outright_repo_serial      TEXT,

    CONSTRAINT pk_debt_outright_repo PRIMARY KEY (date),
    CONSTRAINT fk_debt_outright_repo_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_outright_repo           IS 'PBoC outright-repo tender (bond-buying; longer-tenor liquidity injection).';
COMMENT ON COLUMN stats.debt_outright_repo.outright_repo_marker IS '1 if PBoC announced an outright-repo tender on this date; 0 otherwise.';

-- ----------------------------------------------------------------------------
-- Table: debt_mlf
--   ← PBoC MLF (Medium-term Lending Facility) tender
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_mlf (
    date                      DATE          NOT NULL,
    mlf_marker                SMALLINT NOT NULL DEFAULT 0
        CHECK (mlf_marker IN (0,1)),
    mlf_quantity              NUMERIC(18,4),
    mlf_tenor_days            NUMERIC(6,1),
    mlf_tenor_label           TEXT,
    mlf_serial                TEXT,

    CONSTRAINT pk_debt_mlf PRIMARY KEY (date),
    CONSTRAINT fk_debt_mlf_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_mlf                     IS 'PBoC MLF (Medium-term Lending Facility) tender.';
COMMENT ON COLUMN stats.debt_mlf.mlf_marker          IS '1 if PBoC announced an MLF tender on this date; 0 otherwise.';

-- ----------------------------------------------------------------------------
-- Table: debt_shibor
--   ← SHIBOR daily fixings
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_shibor (
    date                      DATE          NOT NULL,
    shibor_o_n                NUMERIC(8,4),
    shibor_1w                 NUMERIC(8,4),
    shibor_2w                 NUMERIC(8,4),
    shibor_1m                 NUMERIC(8,4),
    shibor_3m                 NUMERIC(8,4),
    shibor_6m                 NUMERIC(8,4),
    shibor_9m                 NUMERIC(8,4),
    shibor_1y                 NUMERIC(8,4),

    CONSTRAINT pk_debt_shibor PRIMARY KEY (date),
    CONSTRAINT fk_debt_shibor_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_shibor                  IS 'SHIBOR daily fixings (all in %).';

-- ----------------------------------------------------------------------------
-- Table: debt_treasury
--   ← China treasury bond yield curve
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_treasury (
    date                      DATE          NOT NULL,
    cb_0d                     NUMERIC(8,4),
    cb_1m                     NUMERIC(8,4),
    cb_2m                     NUMERIC(8,4),
    cb_3m                     NUMERIC(8,4),
    cb_6m                     NUMERIC(8,4),
    cb_9m                     NUMERIC(8,4),
    cb_1y                     NUMERIC(8,4),
    cb_2y                     NUMERIC(8,4),
    cb_3y                     NUMERIC(8,4),
    cb_5y                     NUMERIC(8,4),
    cb_7y                     NUMERIC(8,4),
    cb_10y                    NUMERIC(8,4),
    cb_15y                    NUMERIC(8,4),
    cb_20y                    NUMERIC(8,4),
    cb_30y                    NUMERIC(8,4),
    cb_40y                    NUMERIC(8,4),
    cb_50y                    NUMERIC(8,4),

    CONSTRAINT pk_debt_treasury PRIMARY KEY (date),
    CONSTRAINT fk_debt_treasury_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_treasury                IS 'China treasury bond yield curve (all in %).';
COMMENT ON COLUMN stats.debt_treasury.cb_30y         IS '30Y CGB yield (%) — proxy for long-duration risk-free rate.';

-- ----------------------------------------------------------------------------
-- Table: debt_lpr
--   ← PBoC LPR (Loan Prime Rate) monthly announcement
--   LPR has two tenors: 1Y and 5Y+ (5年期以上). Published monthly on the 20th
--   (or next business day if holiday) since the new formation mechanism
--   started on 2019-08-20. One row per announcement date.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.debt_lpr (
    date                      DATE          NOT NULL,
    lpr_1y                    NUMERIC(8,4),
    lpr_5y                    NUMERIC(8,4),

    CONSTRAINT pk_debt_lpr PRIMARY KEY (date),
    CONSTRAINT fk_debt_lpr_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_lpr                     IS 'PBoC LPR (Loan Prime Rate) monthly announcement — 1Y and 5Y+ tenors (all in %).';
COMMENT ON COLUMN stats.debt_lpr.lpr_1y              IS '1Y LPR (%) — benchmark for short-term bank lending rates.';
COMMENT ON COLUMN stats.debt_lpr.lpr_5y              IS '5Y+ LPR (%) — benchmark for mortgage and long-term lending rates.';

-- ----------------------------------------------------------------------------
-- Table: pboc_oma
--   ← PBoC Open Market Announcements (公开市场业务公告)
--   High-level policy announcements from the PBoC Open Market Operations desk
--   (https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125469/index.html).
--   These are NOT the daily transaction announcements (those live in debt_omo /
--   debt_outright_repo / debt_mlf via instruments_combined.csv); instead they
--   are infrequent policy notices such as:
--     · 隔夜逆回购 (overnight reverse repo) tool scheduling
--     · 买断式逆回购 (outright reverse repo) tool introduction
--     · 一级交易商 (primary dealer) annual list
--     · 央行票据 (central bank bills) issuance policy
--     · 中期借贷便利 (MLF) tool policy changes
--   Multiple announcements can share a pub_date (e.g. [2026]第2号 + [2026]第3号
--   both on 2026-06-17), so the PK is (date, title) and there is NO foreign
--   key to debt_identity (announcements may also occur on non-trading days).
--   Loaded by build_debt_baseline.py from temps/pboc_oma_news/oma_combined.csv
--   produced by download_pboc_oma.py.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.pboc_oma (
    date                      DATE          NOT NULL,
    title                     TEXT          NOT NULL,
    type                      TEXT,
    content                   TEXT,
    detail_url                TEXT,
    keywords                  TEXT,
    serial_year               TEXT,
    serial_no                 TEXT,
    detail_slug               TEXT,

    CONSTRAINT pk_pboc_oma PRIMARY KEY (date, title)
);

COMMENT ON TABLE  stats.pboc_oma                     IS 'PBoC Open Market Announcements (公开市场业务公告) — high-level policy notices, NOT daily transaction announcements.';
COMMENT ON COLUMN stats.pboc_oma.type                IS 'Classified type slug: overnight_reverse_repo | outright_repo | central_bank_bill | mlf | primary_dealer | interest_rate | tool_introduction | other.';
COMMENT ON COLUMN stats.pboc_oma.content             IS 'Full announcement body text (cleaned).';
COMMENT ON COLUMN stats.pboc_oma.keywords            IS 'Pipe-separated matched keywords (e.g. "隔夜逆回购|一级交易商") from the keyword catalogue in download_pboc_oma.py.';