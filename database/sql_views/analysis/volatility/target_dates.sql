-- Volatility pipeline (analysis.volatility) — the dates the target
-- table already carries for the scope (sec_type + codes). The
-- incremental missing-date set is the source-dates view minus this set
-- (host-side, python-date arithmetic — these reads never enter a frame).
-- args: sec_type: value smallint — SEC_TYPE_CODE[sec_type]
--       codes: value text[] — the run's universe
    SELECT DISTINCT v.date
    FROM analysis.volatility v
    WHERE v.sec_type = {{sec_type}}
      AND v.code = ANY({{codes}}::text[])
    ORDER BY v.date
