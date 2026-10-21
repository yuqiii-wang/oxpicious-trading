"""Entry point for ``python -m analysis.pair_pct_change_ratios``.

Materializes analysis.pair_pct_change_ratios as INDUSTRY x INDUSTRY
pairs (grain 1 only, static.label_dim 'pair_grain'; the per-index grain
was dropped in the 2026-10-17 rework): the EXCESS pct change — the
daily measure is the DIFFERENCE of the two industry-aggregate pct
changes (ind_pct(code, d) — ind_pct(benchmark, d); null-skipped
member means), and the stored value is the trailing 5/20/60/255-day
MEAN of the daily differences. No division anywhere (quotient forms
were numerically fragile on near-flat denominators). Null days (either
industry missing data) are skipped, never zero-filled; a window with
>10% null days emits NULL; sampled ONLY on stride-grid dates of the
global index calendar (the corr materialization pattern; stride default
20, PK member so strides coexist).

Modes:

  incremental (default): stride-grid dates with no row yet for the
    stride + the LATEST grid date while within 15 calendar days of the
    frontier (late-landing closes); heals full history when the universe
    grew. No truncate.

  --force: truncates the table, then recomputes and inserts ALL rows.

  --code ID[,ID...] (filtered mode): recompute ALL grid dates for the
    chosen subject INDUSTRY ids and upsert them (no truncate; driven by
    the UI pair selector refresh).

  --stride N (default 20): materialize an alternative grid. Cannot be
    combined with --force (a truncate would wipe the other strides' rows).

The :class:`PairPctChangeRatiosAnalysis` entry class (a
:class:`DataAnalysis` subclass) owns the runtime lifecycle + DB
connection lifecycle. Bootstrap-first layout: the runtime setup runs
before this module's imports.
"""
from __future__ import annotations

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

from _common.data_analysis import DataAnalysis

from analysis.pair_pct_change_ratios.config import (
    INTERVAL_DAYS,
    SOURCE_TABLE,
    TABLE,
)
from analysis.pair_pct_change_ratios.ratios import run_pair_pct_change_ratios

logger = setup_logging("pair_pct_change_ratios")


def _parse_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


class PairPctChangeRatiosAnalysis(DataAnalysis):
    """``python -m analysis.pair_pct_change_ratios`` — stride-grid MAs."""

    title = ("ANALYZE PAIR PCT-CHANGE RATIOS — code vs benchmark "
             "(stride-grid rolling means)")
    component = "pair_pct_change_ratios"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        parser.add_argument(
            "--stride", type=int, default=INTERVAL_DAYS, metavar="N",
            help=f"Grid stride in trading days on the global index "
                 f"calendar (default {INTERVAL_DAYS}). Stored as the "
                 f"PK member \"interval\", so several strides coexist.",
        )
        parser.add_argument(
            "--code", default="", metavar="ID[,ID...]",
            help="Filtered mode: recompute ALL grid dates for these "
                 "subject INDUSTRY ids and upsert them. No truncate.",
        )

    def apply_args(self) -> None:
        self.code_args = _parse_csv(self.args.code)
        if self.args.stride < 1:
            self.parser.error("--stride must be >= 1")
        if self.args.force and self.code_args:
            self.parser.error(
                "--force cannot be combined with --code (filtered runs "
                "never truncate the table)"
            )
        if self.args.force and self.args.stride != INTERVAL_DAYS:
            self.parser.error(
                "--force cannot be combined with a non-default --stride "
                "(a truncate would wipe the other strides' rows)"
            )

    def header_fields(self) -> dict:
        return {
            "source_table": f"{SOURCE_TABLE} closes",
            "table": TABLE,
            "stride": f"{self.args.stride}",
            "mode": "FORCE (full recompute)" if self.args.force
                    else "FILTERED (chosen subjects, recompute + upsert)"
                    if self.code_args
                    else "incremental (missing grid dates only)",
        }

    async def run(self) -> None:
        await run_pair_pct_change_ratios(
            self.conn,
            force=self.args.force,
            stride=self.args.stride,
            codes=self.code_args or None,
        )


if __name__ == "__main__":
    PairPctChangeRatiosAnalysis().execute()
