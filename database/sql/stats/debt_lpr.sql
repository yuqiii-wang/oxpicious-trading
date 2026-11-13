
-- ----------------------------------------------------------------------------
-- Table: debt_lpr
--   ← PBoC LPR (Loan Prime Rate) monthly announcement
--   LPR has two tenors: 1Y and 5Y+ (5年期以上). Published monthly on the 20th
--   (or next business day if holiday) since the new formation mechanism
--   started on 2019-08-20. One row per announcement date.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The monthly loan prime rate announcement — the 1Y and 5Y+ lending benchmarks.
-- tags: debt, 债券, lpr, 贷款市场报价利率, rates, 利率, central bank, 央行

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
