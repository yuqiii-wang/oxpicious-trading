-- Composites filtered mode: which --code member index codes carry an
-- index classification row (the unmapped-code warning's read).
-- args:
--   codes: value text[] — the requested member index codes
SELECT code FROM stats.sec_classification WHERE type = 'index' AND code = ANY({{codes}})
