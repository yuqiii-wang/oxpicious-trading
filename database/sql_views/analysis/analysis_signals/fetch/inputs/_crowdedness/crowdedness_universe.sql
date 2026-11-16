-- The crowdedness family's DATA-DRIVEN universe (the options-families
-- precedent): distinct codes with crowdedness rows at the sec_type —
-- the pools the nightly analysis.market_crowdedness build covers
-- (index 413 codes / industry 71; etf/stock have no crowdedness data,
-- so the family skips those sec_types entirely).
-- args:
--   sec_type_code: fragment — the sec_type smallint literal
        SELECT DISTINCT code
        FROM analysis.market_crowdedness
        WHERE sec_type = {{sec_type_code}}
        ORDER BY code ASC
