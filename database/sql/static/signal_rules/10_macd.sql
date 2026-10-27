-- macd — the family knowledge document (see 00_schema.sql).
-- 2026-10-26 first seed: the classic MACD signal-line crossover as a
-- rule cell (the typical-EMA study's product — the most typical EMA
-- usage is the classic MACD set 12/26/9; the tech_stats tables carry
-- ema12/ema26 since the same day, replacing the nearby non-standard
-- span 10).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "source": "macd = ema12 - ema26 READ from stats.{etf,index,stock}_tech_stats (the builds' precomputed typical EMAs, LEFT JOINed per code+date — never a self-customized EMA recompute; the strategy-tier MacdAlgo's source verbatim); signal = EMA9(macd) computes inline (an EMA of the MACD line has no precomputed column); hist = macd - signal; the recorded value is the histogram as pct of close, reference 0.0; the family's incremental-skip horizon is the tech_stats max(date)",
  "sub_signals": {
    "macd_cross_up": {
      "meaning": "The MACD line crosses above its EMA9 signal line — the classic bullish crossover, the momentum turn",
      "trigger": "macd > signal AND prev_macd <= prev_signal (the classic 12/26/9 spans)"
    },
    "macd_cross_dn": {
      "meaning": "The MACD line crosses below its EMA9 signal line — the classic bearish crossover",
      "trigger": "macd < signal AND prev_macd >= prev_signal (the classic 12/26/9 spans)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'macd'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
