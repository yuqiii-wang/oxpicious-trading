-- bollinger — the family knowledge document (see 00_schema.sql).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "boll_std",
  "description": "The Bollinger fixed-sigma family: a BUY fires when the close's sigma-depth z = (price - SMA20)/sigma20 pierces -k (the lower band); a SELL at +k (the upper band). Bars are FIXED sigma multipliers — z already IS the Gaussian depth score, so a percentile layer would restate the same axis. Population sigma (ddof=0) matches the charting convention.",
  "condition": "sigma-depth z = (price - SMA20)/sigma20 beyond +/-k sigma, k in {2.0, 2.5, 3.0}, z computed in-engine from the fetched price",
  "sources": [
    "stats.index_basic_stats.close (the price axis, sec-type dependent)",
    "stats.market_regimes.regime"
  ],
  "principles": {
    "asymmetry": "The sell side is gated at 0 (exit timing); the buy needs a measured edge (0.75% blended dir_ave at floor 4 episodes).",
    "episodes": "One signal per episode at the FIRST day, K=5 out-of-band traded-day absorb on the code's own calendar.",
    "nested_carving": "The bands carve deepest-first: a deep breach counts once, at its deepest claim (3 sigma carves out of 2.5, 2.5 out of 2).",
    "band_walks": "A stepped-back-in re-breach is trend continuation (the band walk / falling knife), not a fresh reversal claim — the K-absorb keeps it inside the same episode.",
    "regimes": "Regimes are SPLITS, never weights: mean reversion concentrates in elevated-vol states (hot/panic)."
  },
  "sub_signals": {
    "boll20_2std": {
      "meaning": "The classic 20/2sigma breach — the default Bollinger setup",
      "trigger": "z < -2 (buy) / z > +2 (sell), nested under the deeper bands",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 20,
        "k": 2.0,
        "sigma": "population ddof=0"
      }
    },
    "boll20_2p5std": {
      "meaning": "The 2.5-sigma breach, excluding days already claimed by 3 sigma",
      "trigger": "z < -2.5 (buy) / z > +2.5 (sell), nested under 3 sigma",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 20,
        "k": 2.5,
        "sigma": "population ddof=0"
      }
    },
    "boll20_3std": {
      "meaning": "The deep 3-sigma breach — the deepest single claim",
      "trigger": "z < -3 (buy) / z > +3 (sell)",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 20,
        "k": 3.0,
        "sigma": "population ddof=0"
      }
    }
  }
}$json$::jsonb,
       '2026-10-14'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'bollinger'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
