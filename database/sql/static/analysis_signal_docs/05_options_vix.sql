-- options_vix — the family knowledge document (see 00_schema.sql).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "options_vix",
  "description": "The vol-spike-in-fear-regime family: BUY THE PANIC, conditionally. Axes: corr20 (the PUBLISHED 20-session corr of spot log-return vs vol log-change — analysis.options_vol_index_vs_spot_price_corr, linearly interpolated per code onto every session IN-ENGINE; below -0.8 is roughly the 5th-percentile deep-fear tail), slope5 (the 5-day vol slope), z252 (the vol level z). The measured premise: corr alone is baseline, spike alone weak — the INTERACTION is the edge. The vol30-space PEAK leg (ovix_panic_peak, 2026-10-15) answers the diagnosis that the corr axis stays moderate through V-spike crises and level-based conditions fire at crisis ONSET: it re-arms at the NEW vol extreme with a downside leg.",
  "condition": "the corr x spike interaction inside the deep-fear regime (buy), the vol-ATH escalation at the panic PEAK (buy, vol30 value space), and the confirmed fear-regime normalization (sell)",
  "sources": [
    "analysis.options_vol_index.vol_index_30d",
    "analysis.options_spot_daily.close",
    "analysis.options_vol_index_vs_spot_price_corr.corr_20d (published stride samples, linearly interpolated to daily in-engine — never interpolated in the DB)"
  ],
  "principles": {
    "interaction_is_the_edge": "Never productize a single-axis variant: corr alone and spike alone measure at baseline; the combination carries the edge (the panic_peak leg is itself a 3-axis interaction: vol-ATH escalation x still-rising slope x downside spot).",
    "exits": "Exit rides corr normalization (corr20 back above -0.5) — never tight stops (a -8% stop halves the win rate), max hold ~60 sessions.",
    "pooled": "Sparse crisis population (11-25 events over 7 years): POOLED — trend 4 / regime 4; confidence FLAT.",
    "crisis_dedup": "Episodes dedup at the crisis scale (K=8): crisis regimes fire daily; one signal per crisis episode. The panic_peak leg is the ESCALATION exception: a vol push >= 5% past the prior 120-session max sits > 8 sessions past the previous qualifying day by construction, so each crisis LEG that sets a new extreme fires its own episode (000852 2024-01-18 then 2024-02-05)."
  },
  "sub_signals": {
    "ovix_spike": {
      "meaning": "The panic spike inside the deep-fear regime",
      "trigger": "corr_20d < -0.8 AND slope5 > +10%",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "corr_bar": -0.8,
        "slope5_gt": 0.1,
        "rule_entry": "OvixSpikeEntry"
      }
    },
    "ovix_spike_hivol": {
      "meaning": "The spike with elevated vol LEVEL — the interaction at its strongest",
      "trigger": "corr_20d < -0.8 AND slope5 > +10% AND z252 > 1",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "corr_bar": -0.8,
        "slope5_gt": 0.1,
        "z252_gt": 1.0,
        "rule_entry": "OvixSpikeEntry"
      }
    },
    "ovix_panic_peak": {
      "meaning": "The panic PEAK escalation — vol pushes to a new 120-session extreme (>= +5% past the prior max) while still spiking and spot sits below its 5-sessions-ago close. Catches the crisis's max-fear/min-spot days the corr-gated configs structurally miss: in V-spike crises (000852 Feb-2024: interp corr bottomed -0.74 vs the -0.8 bar) the corr regime stays moderate, and any level-based condition's episode fires at crisis ONSET (mid-Jan) with K=8 absorbing the peak.",
      "trigger": "vol30 > prior-120-session max x 1.05 AND slope5 > +10% AND close < close 5 sessions ago",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "ath_window": 120,
        "ath_margin": 0.05,
        "slope5_gt": 0.1,
        "downside_window": 5,
        "value_space": "vol_index_30d",
        "rule_entry": "OvixPanicPeakEntry",
        "measured": "pooled n=14 dir_ave +3.55% hit 86%; per-code 000852 n=5 +4.86% @80 (2024-01-18, 2024-02-05, 2023-08-25, 2026-02-02, 2026-07-20), 000300 n=4 +2.32% @75; the downside leg excludes the upside-euphoria vol spikes (2024-10-08 stimulus top)"
      }
    },
    "ovix_fear_unwind": {
      "meaning": "The panic premium is gone and the fear trade is over — the confirmed exit",
      "trigger": "corr_20d crosses back above -0.5 with a 40-session deep-fear witness (trailing corr minimum < -0.8)",
      "action": {
        "sell": "exit timing"
      },
      "params": {
        "unwind_corr": -0.5,
        "witness_sessions": 40,
        "rule_entry": "OvixFearUnwindEntry"
      }
    }
  }
}$json$::jsonb,
       '2026-10-15'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'options_vix'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
