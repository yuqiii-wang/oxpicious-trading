"""analysis.signal_rules_audit — configuration constants."""
from __future__ import annotations

TYPE_AUDIT_TABLE = "analysis_signals.signal_type_audit"
OVERLAP_AUDIT_TABLE = "analysis_signals.signal_overlap_audit"

ANALYSIS_NAME = "signal_rules_audit"

DESCRIPTION = (
    "Signal rules audit over the standalone signal families' history "
    "(analysis_signals.history_signals standalone trading tier): per "
    "YEAR per family — trigger-day counts, buy/sell split, code/day "
    "spread and the share of the period's signal universe "
    "(signal_type_audit); per YEAR per ORDERED family pair — the "
    "overlap (of A's trigger days, the fraction B also fired on), the "
    "chance expectation, the lift, Jaccard and the same/opposite-action "
    "splits (signal_overlap_audit). Read by the Composites → Signal "
    "Rules Audit page."
)

# Population contract (standalone trading signals only) lives in the
# signal_events / signal_years view files + the DDL banners — never a
# Python-side copy of the WHERE clause.

# Incremental recompute slack: the most recent N COMPLETE years (plus
# the trailing current year, always) recompute every run — late writes
# to history_signals (a --force'ed rebuild, a new family graduating)
# correct the just-closed years. Past years outside the grace compute
# once. --force recomputes every event-bearing year.
GRACE_YEARS = 1

# The audit's default security scope (the study scope: index).
DEFAULT_SEC_TYPE = "index"

SEC_TYPE_CHOICES = ("index", "etf", "stock")
