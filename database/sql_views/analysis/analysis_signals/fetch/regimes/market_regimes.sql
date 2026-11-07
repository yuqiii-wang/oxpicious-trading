-- The sec_type's DAILY market-regime states (stats.market_regimes, the
-- 4-state label: calm / hot / panic / quiet), bounded to date >= since —
-- one row per (code, date); the engine merges it onto the input frame by
-- (code, date).
-- args:
--   sec_type: value int — the sec_type smallint label code
--   since: value date — the lower date bound
        SELECT m.code,
               extract(epoch from m.date)::float8 AS date,
               m.regime::int AS regime
        FROM stats.market_regimes m
        WHERE m.sec_type = {{sec_type}}
          AND m.date >= {{since}}
        ORDER BY m.code, m.date ASC
