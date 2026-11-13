
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
--   Loaded by builds/bond (pboc_oma.py) from temps/pboc_oma_news/oma_combined.csv
--   produced by download_pboc_oma.py.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The central bank's open-market policy announcements — the high-level notices that set the liquidity stance.
-- tags: central bank, 央行, policy, 政策, announcement, 公告, liquidity, 流动性

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
