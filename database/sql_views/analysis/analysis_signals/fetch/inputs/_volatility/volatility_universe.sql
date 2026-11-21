-- The volatility family's DATA-DRIVEN universe (the crowdedness /
-- options-families precedent): distinct codes with realized-vol rows
-- at the sec_type — the codes the analysis.volatility panel covers
-- (index only while the panel's run scope is the 10 study indices;
-- etf/stock have no vol rows, so the family skips those sec_types
-- entirely).
-- args:
--   sec_type_code: fragment — the sec_type smallint literal
        SELECT DISTINCT code
        FROM analysis.volatility
        WHERE sec_type = {{sec_type_code}}
        ORDER BY code ASC
