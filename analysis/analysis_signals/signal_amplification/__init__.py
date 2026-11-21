"""Signal amplification — the amplifier tier
(analysis.analysis_signals.signal_amplification, 2026-11-18; the
2026-11-20 two-level amplifier axis + the margin type).

Every trigger in the nine standalone signal families reads the CLOSE
(RSI, σ-depth, MA gap, momentum, MACD histogram); the intraday
open/high/low and the volume (trading_amount) ride along unused. This
tier records the recurring day-level anatomy — the AMPLIFIER — and
binds it to the signal families it strengthens or discounts. The
amplifier axis is TWO-LEVEL: the PARENT TYPE (static.label_dim
'signal_amplifier_type' — 0 ohlcv, the K-line day anatomy / 1 margin,
the 融资融券 margin-balance level) + the per-type SUB TYPE vocabulary
('signal_amplifier' when ohlcv, 'signal_amplifier_margin' when margin):

  analysis.ohlcv_ext                       the extended per-day tech
                                           stats this tier builds
                                           self-contained from
                                           stats.{index,etf,stock}_basic_
                                           stats (gaps / wicks / close
                                           position / prev-day refs /
                                           amt ratios) — the ohlcv
                                           amplifiers' data source
  (in-run)                                 the margin anatomy
                                           (margin_anatomy.MarginAnatomy)
                                           — the trailing rz_balance
                                           quantile bars from
                                           stats.{etf,stock}_liquidity_
                                           margin / analysis.margin_
                                           index_series, merged onto the
                                           anatomy frame, never persisted
  analysis_signals.signal_amplification_rules    one row per (code,
                                           sec_type, signal_type) — the
                                           binding config (amplifiers[],
                                           bars, base_bp + measured
                                           evidence), wiped + reinserted
                                           per run
  analysis_signals.signal_amplification_history  the ORIGINAL RECORD
                                           KEEPER — one row per
                                           amplifier day (code, sec_type,
                                           amplifier_type, sub_type,
                                           date) with the day's anatomy
                                           facts + the per-binding
                                           amplification applied

Layout (class-first, the oxpicious-dev-conventions preference):

  anatomy.py     OhlcvAnatomy   — the ohlcv_ext column compute
  ohlcv_ext.py   OhlcvExtBuilder — the source fetch + incremental write
  margin_anatomy.py MarginAnatomy — the margin series fetch + the
                 trailing balance quantile bars (the 'margin' type)
  amplifiers.py  AmplifierDef / AMPLIFIER_DEFS / AmplifierEngine —
                 the two-level amplifier registry + the day
                 masks/depths
  measurement.py AmplificationEvidence — the per-binding measured
                 evidence (amplifier-day forward response vs the bound
                 family's own base)
  run.py         run_amplification — the orchestration + writes

Run inside the analysis_signals daily pipeline (after the families —
the evidence reads their history_signals events) or standalone:
  python -m analysis.analysis_signals.signal_amplification
"""
