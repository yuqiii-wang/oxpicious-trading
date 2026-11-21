"""analysis.oracle_signals — the hindsight ORACLE tier.

DECOUPLED from the analysis_signals rule tier (no rule cells, no
replay, no live wiring): this package looks at FUTURE daily bars and
records the perfect-foresight trade decision per 5-trading-day unit —
``analysis.oracle_signals`` (see
database/sql/analysis/oracle_signals.sql for the decision grammar).
"""
