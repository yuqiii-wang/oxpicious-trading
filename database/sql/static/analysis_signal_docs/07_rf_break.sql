-- rf_break — the family knowledge document (see 00_schema.sql).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "Roof/Floor",
  "description": "The roof/floor breakout family: a BUY fires when the close crosses ABOVE the prior day's roof level (the significant-high continuation, turtle System-1 style); a SELL when it crosses BELOW the prior floor (the breakdown exit timing). The bars are STRUCTURAL — window extremes / the cross-method median consensus from the roof/floor estimates store, not percentile tails — which is the point: the 2024-09-30 / 2026-10-15 studies showed the per-code trailing-tail bars and the (trend, regime) cell grid starving exactly at the extreme corners where significant highs and lows land, while the options-wall family's fixed wall bar caught the same days. Grid: (method, window, side) — donchian (the window's raw max intraday high / min low) and median (the NaN-skipping median of the five estimate methods per (date, period), the smoother consensus) x {60, 120, 255, 500} trading days x roof/floor.",
  "condition": "close vs the PRIOR day's roof/floor_estimate (analysis.mov_ave_spreads_roof_floor_estimates, methods donchian=1 and median=5), windows {60, 120, 255, 500}; NO cross-window carving — each (method, window) breach is that sub type's own population (the windows are different claim strengths); regime-free with a trend split; K=5 episode absorb on the code's own traded days",
  "sources": [
    "analysis.mov_ave_spreads_roof_floor_estimates.roof_estimate (methods donchian, median)",
    "analysis.mov_ave_spreads_roof_floor_estimates.floor_estimate (methods donchian, median)",
    "analysis.mov_ave_spreads_roof_floor_estimates.today_close",
    "analysis.trend_days.trend"
  ],
  "principles": {
    "structural_bar": "The breakout bar is the window's own extreme (donchian) or the five-method consensus (median), shifted one row so the trigger day never sets its own bar — no trailing percentile to thin out exactly when the market goes one-sided, the failure mode the two event studies measured.",
    "median_consensus": "The median method smooths the lone-donchian wick extreme: the consensus of {trendline, donchian, quantile, channel, boll} is the level five estimators agree on — breaks of it are the broader-based claims; it catches days the raw wick extreme misses (and vice versa), which is why the two methods carve independently.",
    "asymmetry": "The roof break is a BUY (sharp rises continue — the significant-high day is the entry); the floor break is a SELL (exit timing for longs, never a short entry), the same equity asymmetry the other families carry.",
    "no_cross_window_carving": "Each (method, window) breach fires its own sub type — a new 60d high and the simultaneous new 255d high are different facts. The carve-out variant (deepest-window-claims) was measured first and STARVED the mid windows: the 255d cells lost their strongest days to the 500d claim and fell below the gate floor (the 2024-09-30 events vanished with them).",
    "regime_free": "Per-regime cells measured thin and sign-flippy (the ma_cross precedent) — and the studies' starving corners were regime-corner artifacts: (top, hot) had no sell cells, (bottom, calm) no buy cells.",
    "episodes": "One signal per episode at the FIRST day, K=5 absorb on the code's own traded days — a breakout run flags once, at its onset."
  },
  "sub_signals": {
    "rf60_roof": { "meaning": "The first close above the prior day's 60-day donchian roof", "trigger": "close > roof60[t-1]", "action": { "buy": "continuation — the significant-high day" }, "params": { "window": 60, "method": "donchian", "shift": 1 } },
    "rf120_roof": { "meaning": "The first close above the prior day's 120-day donchian roof", "trigger": "close > roof120[t-1]", "action": { "buy": "continuation — the significant-high day" }, "params": { "window": 120, "method": "donchian", "shift": 1 } },
    "rf255_roof": { "meaning": "The first close above the prior day's 255-day donchian roof", "trigger": "close > roof255[t-1]", "action": { "buy": "continuation — the significant-high day" }, "params": { "window": 255, "method": "donchian", "shift": 1 } },
    "rf500_roof": { "meaning": "The first close above the prior day's 500-day donchian roof — the two-year high", "trigger": "close > roof500[t-1]", "action": { "buy": "continuation — the significant-high day" }, "params": { "window": 500, "method": "donchian", "shift": 1 } },
    "rf60_floor": { "meaning": "The first close below the prior day's 60-day donchian floor", "trigger": "close < floor60[t-1]", "action": { "sell": "exit timing — the breakdown" }, "params": { "window": 60, "method": "donchian", "shift": 1 } },
    "rf120_floor": { "meaning": "The first close below the prior day's 120-day donchian floor", "trigger": "close < floor120[t-1]", "action": { "sell": "exit timing — the breakdown" }, "params": { "window": 120, "method": "donchian", "shift": 1 } },
    "rf255_floor": { "meaning": "The first close below the prior day's 255-day donchian floor", "trigger": "close < floor255[t-1]", "action": { "sell": "exit timing — the breakdown" }, "params": { "window": 255, "method": "donchian", "shift": 1 } },
    "rf500_floor": { "meaning": "The first close below the prior day's 500-day donchian floor — the two-year low", "trigger": "close < floor500[t-1]", "action": { "sell": "exit timing — the breakdown" }, "params": { "window": 500, "method": "donchian", "shift": 1 } },
    "rf60_med_roof": { "meaning": "The first close above the prior day's 60-day median-consensus roof", "trigger": "close > mroof60[t-1]", "action": { "buy": "continuation — the consensus break" }, "params": { "window": 60, "method": "median", "shift": 1 } },
    "rf120_med_roof": { "meaning": "The first close above the prior day's 120-day median-consensus roof", "trigger": "close > mroof120[t-1]", "action": { "buy": "continuation — the consensus break" }, "params": { "window": 120, "method": "median", "shift": 1 } },
    "rf255_med_roof": { "meaning": "The first close above the prior day's 255-day median-consensus roof", "trigger": "close > mroof255[t-1]", "action": { "buy": "continuation — the consensus break" }, "params": { "window": 255, "method": "median", "shift": 1 } },
    "rf500_med_roof": { "meaning": "The first close above the prior day's 500-day median-consensus roof", "trigger": "close > mroof500[t-1]", "action": { "buy": "continuation — the consensus break" }, "params": { "window": 500, "method": "median", "shift": 1 } },
    "rf60_med_floor": { "meaning": "The first close below the prior day's 60-day median-consensus floor", "trigger": "close < mfloor60[t-1]", "action": { "sell": "exit timing — the consensus breakdown" }, "params": { "window": 60, "method": "median", "shift": 1 } },
    "rf120_med_floor": { "meaning": "The first close below the prior day's 120-day median-consensus floor", "trigger": "close < mfloor120[t-1]", "action": { "sell": "exit timing — the consensus breakdown" }, "params": { "window": 120, "method": "median", "shift": 1 } },
    "rf255_med_floor": { "meaning": "The first close below the prior day's 255-day median-consensus floor", "trigger": "close < mfloor255[t-1]", "action": { "sell": "exit timing — the consensus breakdown" }, "params": { "window": 255, "method": "median", "shift": 1 } },
    "rf500_med_floor": { "meaning": "The first close below the prior day's 500-day median-consensus floor", "trigger": "close < mfloor500[t-1]", "action": { "sell": "exit timing — the consensus breakdown" }, "params": { "window": 500, "method": "median", "shift": 1 } }
  }
}$json$::jsonb,
       '2026-10-16'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'rf_break'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
