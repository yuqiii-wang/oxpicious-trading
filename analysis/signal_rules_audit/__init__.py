"""analysis.signal_rules_audit — the signal rules audit.

Audits the standalone signal families' HISTORY (analysis_signals.
history_signals standalone trading tier) at two grains:

  - analysis_signals.signal_type_audit   — per YEAR period per family:
    trigger-day counts, buy/sell split, code/day spread, the share of
    the period's signal universe;
  - analysis_signals.signal_overlap_audit — per YEAR period per ORDERED
    pair (A = base, B = other): how much of A's history is also
    observed in B's (overlap_pct = |A∩B| / |A|), against the chance
    expectation (expected_pct = |B| / universe) — the lift, Jaccard and
    same/opposite-action splits judge whether a co-firing is structural
    or chance, and where families conflict (buy on another's sell).

Read by the Composites → Signal Rules Audit (信号规则审计) page
(/api/analysis/signals-audit/*).
"""
