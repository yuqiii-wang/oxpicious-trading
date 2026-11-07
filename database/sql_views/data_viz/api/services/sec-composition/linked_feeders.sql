-- Linked Feeder Funds — ETF联接基金 whose target (master) ETF is the code,
-- walked via stats.sec_etf_links by target_etf_code (the ETF-level mirror
-- of the linked-etfs parent_index_code lookup). ETF codes are BARE (a
-- suffixed param is stripped); target_etf_code is stored BARE — direct
-- equality. latest — most-recent v_etf_margin row per BARE feeder code (the
-- NAV series builds.etf.feeder_nav loads; feeders are close-only,
-- is_ohl_estimated=TRUE). Distinct-on mirrors linked_etfs.sql.
-- Serves GET /api/sec-composition/linked-feeders.
-- args:
--   target_etf_code: value text — the master ETF's bare code
    WITH latest AS (
      SELECT DISTINCT ON (code) code,
             date AS latest_date, close AS latest_nav, pct_change AS day_pct
        FROM stats.v_etf_margin
       WHERE code IN (SELECT feeder_code FROM stats.sec_etf_links
                      WHERE target_etf_code = {{target_etf_code}})
       ORDER BY code, date DESC
    )
    SELECT l.feeder_code,
           l.feeder_name,
           l.is_initiator,
           l.target_etf_code,
           l.target_etf_name,
           l.target_etf_type,
           l.index_code,
           l.index_name,
           l.manager,
           l.aum_yi,
           l.inception_date::text AS inception_date,
           COALESCE(n.latest_date::text, '') AS latest_date,
           n.latest_nav,
           n.day_pct
      FROM stats.sec_etf_links l
      LEFT JOIN latest n ON n.code = l.feeder_code
     WHERE l.target_etf_code = {{target_etf_code}}
     ORDER BY l.feeder_code
