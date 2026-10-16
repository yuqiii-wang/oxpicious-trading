-- options_wall — the family knowledge document (see 00_schema.sql).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "options_wall",
  "description": "The VRP-gated OI-wall family: the chain-dominant option wall per side (call_wall_high the outer cap, put_wall_low the outer edge); a close MEANINGFULLY past a wall is a reversion trade back toward it. The put side is VRP-GATED — implied vol30 must sit in its trailing top quintile vs realized (the variance-risk-premium confirmation): a breach with RICH IV is the capitulation rebound, with cheap IV it is the falling knife (recorded as the wait _rej twin, never a trading row).",
  "condition": "close beyond the same-day chain-dominant wall edge by 0.25% (put side VRP-gated; call side ungated)",
  "sources": [
    "analysis.options_spot_daily.close",
    "analysis.options_walls (zone walls, dominant per side by strength_score)",
    "analysis.options_vol_index.vol_index_30d (the VRP implied leg)"
  ],
  "principles": {
    "the_gate_is_the_signal": "The put breach is NEVER emitted ungated — the VRP gate is the difference between the capitulation rebound and the falling knife; the VRP-fail population is the observational _rej twin (action wait).",
    "strict_episodes": "Strict consecutive-day episodes (K=0): any day back inside the wall edge starts a fresh episode — the study's median episode is 1 day.",
    "pooled": "Sparse event population (per-code occurrence 1-6/yr): POOLED — trend 4 / regime 4, per-code measured stats ride the row; confidence FLAT (no depth scaling).",
    "meaningful_breach": "Only a breach (not proximity) claims the reversion trade — wall proximity is practitioner folklore, the measured edge lives in the VRP interaction."
  },
  "sub_signals": {
    "ow_put_025pct": {
      "meaning": "The VRP-gated put-wall breach — the capitulation-rebound trade",
      "trigger": "close <= put_wall_low x (1 - 0.25%) AND vrp_ratio >= its trailing-255d 80th-percentile bar (shifted)",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "width_pct": 0.25,
        "vrp_pctl_min": 0.8,
        "vrp_window_days": 255,
        "rule_entry": "WallBreachEntry"
      }
    },
    "ow_put_025pct_rej": {
      "meaning": "The VRP-FAIL twin — the same breach with cheap implied vol (the falling knife); observational only",
      "trigger": "close <= put_wall_low x (1 - 0.25%) AND the VRP gate failed",
      "action": {
        "wait": "never a trading row"
      },
      "params": {
        "width_pct": 0.25,
        "vrp_gate_failed": true,
        "rule_entry": "WallBreachEntry"
      }
    },
    "ow_call_025pct": {
      "meaning": "The call-wall breach — the exhaustion exit above the chain's outer cap, ungated",
      "trigger": "close >= call_wall_high x (1 + 0.25%)",
      "action": {
        "sell": "exit timing"
      },
      "params": {
        "width_pct": 0.25,
        "rule_entry": "WallBreachEntry"
      }
    }
  }
}$json$::jsonb,
       '2026-10-14'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'options_wall'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
