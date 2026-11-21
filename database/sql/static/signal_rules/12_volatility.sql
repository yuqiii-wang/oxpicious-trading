-- volatility — the family knowledge document (see 00_schema.sql).
-- 2026-11-18 seed: the realized-volatility panel (analysis.volatility)
-- as rule cells — the leverage-effect tail family (research premise:
-- "local top volatility signals local low price" — realized-vol peaks
-- cluster at price troughs, Black 1976 / French-Schwert-Stambaugh
-- 1987, and GARCH clustering makes a top-tail vol breach an episode,
-- not a regime). SAME-DAY REWORK (user-directed "too few signals — a
-- rough check on 000001 volatility should have 2 dozens local
-- volatility spikes"): the first grid's all-history (2520d) tails were
-- DEGENERATE on the panel's ~7y history — only the 2024-10
-- mega-cluster ever breached them (000001: 4 events total); the
-- family now trades the LOCAL trailing-252d quantiles over the wider
-- {5,10,15,20}% grid (the panel's own pct_rank_252 convention), which
-- both matches the visible spikes and keeps the edge (pooled over the
-- 10 study indices every tail clears the buy bar hugely: study
-- temp_scripts/vol_spike_count_study.py, +1.15..+1.88% blend,
-- t=3.3..4.9, hit 57-67%).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "source": "the realized-volatility panel analysis.volatility, LEFT JOINed per code+sec_type+date onto the base OHLCV frame (the panel is the daily per-security vol build — hv across 5/10/20/60/120/252-row windows, the RiskMetrics EWMA vol, the OHLC estimators, ATR/NATR, percentile/z gauges); the two trigger axes are hv_20 (the canonical 20-row close-to-close realized vol) and ewma_vol (the RiskMetrics fast vol, λ=0.94 — reacts in days); the response measured is the code's own forward blend (the trade is the security, the vol columns are the trigger); the adaptive tail BARS compute in-engine per code — LOCAL trailing-252-row quantiles, min 60 obs, shifted one row (the panel's own pct_rank_252 convention; NOT all-history — the first grid's 2520d tails were degenerate on the ~7y panel history); the family's universe is data-driven (the codes with volatility rows at the sec_type — index only while the panel's run scope is the study indices) and its incremental-skip horizon is the panel's max(date) at the sec_type",
  "sub_signals": {
    "hv20_5pct": {
      "meaning": "The 20-row realized vol pierces its own trailing-YEAR top 5% tail (the deepest claim — nested carving: a deeper breach never double-counts at a shallower tail). BUY = the local vol spike with the FEAR LEG (price below its 5-rows-ago close — the panic washout, 'local top volatility signals local low price'). SELL = the vol crush at the price stretch (vol below its own BOTTOM 5% tail while price sits within 2% of its trailing-60-row high — complacency at the top, exit timing)",
      "trigger": "BUY: hv_20 > its trailing-252d top-5% adaptive quantile AND price < close 5 rows ago; SELL: hv_20 < its trailing-252d bottom-5% quantile AND price >= 0.98 x the prior 60-row high"
    },
    "hv20_10pct": {
      "meaning": "The same two populations at the wider local 10% tail (claims what the 5% tail has not)",
      "trigger": "BUY: hv_20 > top-10% local quantile (not claimed by 5%) AND the fear leg; SELL: hv_20 < bottom-10% local quantile AND the trailing-high stretch"
    },
    "hv20_15pct": {
      "meaning": "The same two populations at the local 15% tail",
      "trigger": "BUY: hv_20 > top-15% local quantile (not claimed by 5%/10%) AND the fear leg; SELL: hv_20 < bottom-15% local quantile AND the trailing-high stretch"
    },
    "hv20_20pct": {
      "meaning": "The same two populations at the widest local 20% tail",
      "trigger": "BUY: hv_20 > top-20% local quantile (not claimed by 5%/10%/15%) AND the fear leg; SELL: hv_20 < bottom-20% local quantile AND the trailing-high stretch"
    },
    "ewma_5pct": {
      "meaning": "The RiskMetrics fast vol (λ=0.94) pierces its own trailing-YEAR top 5% tail with the same direction gates — the fast-reacting twin of hv20 (the EWMA turns in days; the 20-row std needs the window to move)",
      "trigger": "BUY: ewma_vol > top-5% local quantile AND price < close 5 rows ago; SELL: ewma_vol < bottom-5% local quantile AND price >= 0.98 x the prior 60-row high"
    },
    "ewma_10pct": {
      "meaning": "The fast-vol populations at the wider local 10% tail",
      "trigger": "BUY: ewma_vol > top-10% local quantile (not claimed by 5%) AND the fear leg; SELL: ewma_vol < bottom-10% local quantile AND the trailing-high stretch"
    },
    "ewma_15pct": {
      "meaning": "The fast-vol populations at the local 15% tail",
      "trigger": "BUY: ewma_vol > top-15% local quantile (not claimed by 5%/10%) AND the fear leg; SELL: ewma_vol < bottom-15% local quantile AND the trailing-high stretch"
    },
    "ewma_20pct": {
      "meaning": "The fast-vol populations at the widest local 20% tail",
      "trigger": "BUY: ewma_vol > top-20% local quantile (not claimed by 5%/10%/15%) AND the fear leg; SELL: ewma_vol < bottom-20% local quantile AND the trailing-high stretch"
    }
  },
  "principles": {
    "equity_asymmetry": "the BUY is the capitulation washout (the leverage effect puts vol peaks at price troughs); the SELL is EXIT TIMING ONLY (a naked low-vol sell would fire through every calm uptrend — the low-volatility anomaly is cross-sectional, never a time-series sell; the crush sell carries its own price-stretch gate)",
    "bars": "per-code adaptive LOCAL trailing-252-row percentile quantiles, min 60 obs, SHIFTED one row (the panel's own pct_rank_252 convention — 'local spike' = a burst vs the recent year, not the all-history tail; the retired 2520d grid fired 4 events on 000001 where ~2 dozen local spikes are visible)",
    "episodes": "one signal per episode, first day, delay 0; K=5 plain absorb on the code's own traded rows",
    "nested_carving": "widest-first — the deeper tail claims first, a deep breach counts once at its deepest claim (5% > 10% > 15% > 20%)",
    "regime": "REGIME-FREE (pooled) — the vol tail breach already carries the stress state; the TREND split stays (a fall-trend capitulation and a rise-trend vol burst measure differently)"
  }
}$json$::jsonb,
       '2026-11-18'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'volatility'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
