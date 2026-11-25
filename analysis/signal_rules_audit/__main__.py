"""analysis.signal_rules_audit — the signal rules audit entry point.

Audits the standalone signal families' history
(analysis_signals.history_signals standalone trading tier) at two
grains, per YEAR period:

  1. TypeSummaryStep  — analysis_signals.signal_type_audit: per family
     trigger-day counts, buy/sell split, code/day spread, the share of
     the period's signal universe;
  2. OverlapAuditStep — analysis_signals.signal_overlap_audit: per
     ORDERED pair (A = base, B = other) how much of A's history is
     also observed in B's — overlap_pct, the chance expectation
     (expected_pct), the lift, Jaccard, and the same/opposite-action
     splits of the co-firing days.

Incremental (default): a complete year computes once; the trailing
current year and the most recent --grace-years complete years recompute
every run (late history_signals writes / rebuilt histories). Years with
no events never materialize rows and count as covered; years still in
the audit tables but no longer event-bearing are purged (stale rows
from a shrunken history). ``--force`` deletes the sec_type's rows in
both audit tables and recomputes every event-bearing year.

Usage (WSL conda):
  python -m analysis.signal_rules_audit [--force] [--sec-type index]
      [--grace-years N]
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()  # cudf.pandas hook BEFORE pandas first import

from _common.data_analysis import DataAnalysis  # noqa: E402
from _common.db_commons import (  # noqa: E402
    analyze_tables_async,
    get_existing_keys_async,
)
from _common.labels import SEC_TYPE_CODE  # noqa: E402

from analysis.signal_rules_audit.config import (  # noqa: E402
    DEFAULT_SEC_TYPE,
    DESCRIPTION,
    GRACE_YEARS,
    OVERLAP_AUDIT_TABLE,
    SEC_TYPE_CHOICES,
    TYPE_AUDIT_TABLE,
)
from analysis.signal_rules_audit.periods import (  # noqa: E402
    PeriodGrid,
    period_bounds,
)
from analysis.signal_rules_audit.steps import (  # noqa: E402
    OverlapAuditStep,
    TypeSummaryStep,
    _purge_periods,
    fetch_signal_events,
    fetch_signal_years,
)


def _existing_periods(entries: set) -> set[int]:
    """get_existing_keys_async(["period"]) entries → plain year ints."""
    out: set[int] = set()
    for entry in entries:
        out.add(int(entry[0] if isinstance(entry, (tuple, list)) else entry))
    return out


class SignalRulesAuditAnalysis(DataAnalysis):
    """The signal rules audit (family census + pairwise overlap)."""

    title = "SIGNAL RULES AUDIT (family census + pairwise overlap)"
    description = DESCRIPTION
    component = "signal_rules_audit"

    def add_arguments(self, ap) -> None:
        ap.add_argument(
            "--sec-type", choices=list(SEC_TYPE_CHOICES),
            default=DEFAULT_SEC_TYPE,
            help="The audit's security scope. Default: index.",
        )
        self.add_force_arg(ap)
        ap.add_argument(
            "--grace-years", type=int, default=GRACE_YEARS,
            help=f"The most recent N complete years (plus the trailing "
                 f"current year, always) recompute every run. "
                 f"Default: {GRACE_YEARS}.",
        )

    def header_fields(self) -> dict:
        return {
            "mode": (
                "FORCE (sec_type rows deleted, every event year rebuilt)"
                if self.args.force
                else "incremental (missing + trailing + grace years)"
            ),
            "sec_type": self.args.sec_type,
            "grace_years": self.args.grace_years,
        }

    async def run(self) -> None:
        conn = self.conn
        sec_code = int(SEC_TYPE_CODE[self.args.sec_type])

        if self.args.force:
            await self.delete_sec_type_rows(
                [TYPE_AUDIT_TABLE, OVERLAP_AUDIT_TABLE], [sec_code])

        event_years = await fetch_signal_years(conn, sec_code)
        if not event_years:
            print(f"[signal_rules_audit] no standalone trading events at "
                  f"sec_type={self.args.sec_type} — nothing to audit")
            return
        existing = _existing_periods(
            await get_existing_keys_async(conn, TYPE_AUDIT_TABLE, ["period"]))

        grid = PeriodGrid(
            event_years=frozenset(event_years),
            grace_years=self.args.grace_years,
            force=self.args.force,
        )
        targets = grid.targets(existing)
        # Years still materialized but no longer event-bearing (a
        # shrunken/rebuilt history) are stale — purge, never recompute.
        stale = sorted(existing - event_years)
        if not targets and not stale:
            print("[signal_rules_audit] all periods up to date — nothing "
                  "to compute")
            return
        if stale:
            print(f"[signal_rules_audit] purging {len(stale)} stale "
                  f"year(s) with no events: {stale}")
            for table in (TYPE_AUDIT_TABLE, OVERLAP_AUDIT_TABLE):
                await _purge_periods(conn, table, sec_code, stale)

        events = await fetch_signal_events(conn, sec_code)
        print(
            f"[signal_rules_audit] sec_type={self.args.sec_type} "
            f"({sec_code}): {len(targets)} target year(s) of "
            f"{len(grid.all_years())} event-bearing "
            f"({len(event_years - existing)} missing, rest grace/"
            f"trailing); {len(events):,} day observations"
        )

        for period in targets:
            period_start, period_end = period_bounds(period)
            for step in (TypeSummaryStep(), OverlapAuditStep()):
                await step.run(conn, events, sec_code, period,
                               period_start, period_end)
        await analyze_tables_async(conn, [TYPE_AUDIT_TABLE,
                                          OVERLAP_AUDIT_TABLE])


if __name__ == "__main__":
    SignalRulesAuditAnalysis().execute()
