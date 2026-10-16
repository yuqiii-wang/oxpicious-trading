"""analysis.analysis_signals — the SIGNAL RULES tier (redesigned
2026-10-14).

Three tables (database/sql/analysis/analysis_signals/): signal_rules
(the per-(code, config, trend, regime, action) rule cells — the engine
entry name and the SELF-ADAPTIVE prepared params),
static.analysis_signal_docs (the family knowledge, one row per family,
seeded from database/sql/static/analysis_signal_docs/) and
history_signals (the
history populated by REPLAYING those rules).

Layout:
  config/     the shared evaluation contract + family grids
  fetch/      the data-access layer (input frames, regimes)
  (trends)    the TREND classification + episode machinery moved
              2026-10-15 into the consolidated analysis.trends
              component (attach_trend + EpisodeDetector — imported
              from there by rule_base and the trend-days store write)
  engines/    the OOP layout (2026-10-15): rule_base.RuleEntry the
              ABC + run_cells cell loop, evaluation.py the contract
              objects (CellGate / cell_stats),
              boost.py the OhlcvBoost collaborator (default OFF) +
              signal_families/ (the five families' implementations,
                  one engine package each)
"""
