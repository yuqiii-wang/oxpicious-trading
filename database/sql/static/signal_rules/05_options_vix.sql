-- options_vix — the family knowledge document (see 00_schema.sql).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "ovix_spike": {
      "meaning": "The panic spike inside the deep-fear regime",
      "trigger": "corr_20d < -0.75 AND slope5 > +10%"
    },
    "ovix_spike_hivol": {
      "meaning": "The spike with elevated vol LEVEL — the interaction at its strongest",
      "trigger": "corr_20d < -0.75 AND slope5 > +10% AND z252 > 1"
    },
    "ovix_panic_peak": {
      "meaning": "The panic PEAK escalation — vol pushes to a new 120-session extreme (>= +5% past the prior max) while still spiking and spot sits below its 5-sessions-ago close. Catches the crisis's max-fear/min-spot days the corr-gated configs structurally miss: in V-spike crises (000852 Feb-2024: interp corr bottomed -0.74) the corr regime stays moderate, and any level-based condition's episode fires at crisis ONSET (mid-Jan) with K=8 absorbing the peak.",
      "trigger": "vol30 > prior-120-session max x 1.05 AND slope5 > +10% AND close < close 5 sessions ago"
    },
    "ovix_euphoria_peak": {
      "meaning": "The EUPHORIA top — the panic-peak MIRROR (2026-10-19, from the off-spike study): vol pushes to a new 120-session extreme (>= +5% past the prior max) while still spiking and spot sits ABOVE its 5-sessions-ago close. The upside-euphoria class the buy legs deliberately exclude (the 2024-10-08 stimulus top bought the top otherwise), productized as exit timing. The asymmetry holds on the mirror side too: euphoria tops mostly CONTINUE (the other codes' euphoria cells measured out at dir_ave below the 0 bar) — the leg trades only where the blow-off actually rolled over (000852 2024-10-08, +8.15% exit edge at the starvation x0.5).",
      "trigger": "vol30 > prior-120-session max x 1.05 AND slope5 > +10% AND close > close 5 sessions ago"
    },
    "ovix_atr_break": {
      "meaning": "The volatility-EXPANSION breakout — a fast vol repricing from a LOW base (slope5 fires while corr20 sits far above the fear bar and vol30 far below its 120-session max), so the spike and peak legs stay dark; the price leg is the day's close-to-close return in ATR units (|ret| rolling mean, options_spot_daily has no OHLC). Fired 2026-10-15 on 000852 at -2.51 ATR.",
      "trigger": "day return <= -k x ATR20 AND slope5 > +10%; k self-adaptive per cell from {1.5, 2, 2.5, 3} (1.5 added 2026-10-19)"
    },
    "ovix_fear_unwind": {
      "meaning": "The panic premium is gone and the fear trade is over — the confirmed exit",
      "trigger": "corr_20d crosses back above -0.5 with a 40-session deep-fear witness (trailing corr minimum < -0.75)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'options_vix'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
