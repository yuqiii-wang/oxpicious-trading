
-- ----------------------------------------------------------------------------
-- Table: debt_omo
--   ← PBoC Open Market Operations (reverse repo)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The central bank's open-market reverse-repo operations — the size, rate and maturity of each daily liquidity injection.
-- tags: debt, 债券, central bank, 央行, open market operations, 公开市场操作, reverse repo, 逆回购, liquidity, 流动性

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
    omo_rate_1d               NUMERIC(8,4),
    omo_quantity_1d           NUMERIC(18,4),

    CONSTRAINT pk_debt_omo PRIMARY KEY (date),
    CONSTRAINT fk_debt_omo_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_omo                     IS 'PBoC Open Market Operations (reverse repo).';
COMMENT ON COLUMN stats.debt_omo.omo_all_rates       IS 'Comma-separated multi-tender rates (e.g. "2.2,2.4") on days when PBoC ran >1 reverse-repo tender.';
COMMENT ON COLUMN stats.debt_omo.omo_rate_1d         IS 'Overnight reverse-repo rate (%) — the bulletin states quantity only (fixed-rate tender); rate = 7d OMO rate + 25bp per 公开市场业务公告[2026]第2号 (first operation 2026-06-29 at 1.65%).';
COMMENT ON COLUMN stats.debt_omo.omo_quantity_1d     IS 'Overnight reverse-repo operation quantity (亿元), parsed from the daily bulletin rider "同时，开展了N亿元隔夜逆回购操作" (tool introduced 2026-06-29).';
