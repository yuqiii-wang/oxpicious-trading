"""Entry point for analysis.analysis_composites.

Run via ``python -m analysis.analysis_composites``.

Composite analyses in the ``analysis_composites`` schema (see
database/sql/analysis/analysis_composites/):

  - industry_corr_benchmark_offsets: OPPOSITE industry correlations by
    benchmark offset. Each industry's MA trend (mean_close from
    stats.industry_basic_stats) is offset by a broad-market benchmark —
    the benchmark MA is rebased to the industry's MA level at each window
    start (k = MA_X[s] / MA_B[s]) and SUBTRACTED (common market factor
    removed); prices are recomputed from the offset trends (rebased to
    100 at the window start) and pairwise Pearson correlations are
    audited over 20/60/255 trading-day windows next to the RAW
    (overall) correlation, plus the derived opposite score
    (1 - offset_sub_corr) / 2 in [0, 1].

Modes (mirroring analysis.industry_sentiments.corr):

  incremental (default):
    Per benchmark, detects the windows that need (re)computation —
    MISSING complete stride-grid windows plus the TRAILING windows that
    end at the latest calendar date (find_missing_offset_window_ends)
    — and (re)upserts only those. The daily increment therefore only
    ever touches the span between the last frozen stride point and the
    latest date. Superseded trailing rows are deleted. No truncate.

  --force:
    Truncates the table, then recomputes and inserts ALL rows (full
    history, every configured benchmark). Cannot be combined with
    --industry / --code.

  --industry ID[,ID...] and/or --code CODE[,CODE...] (filtered mode):
    Recomputes ALL windows for the pairs among the given industries and
    UPSERTS them (no truncate). ``--industry`` takes industry_ids directly
    (e.g. BANKS,AI); ``--code`` takes member index codes (e.g. 000004,
    000005) which are resolved to industry_ids via stats.sec_classification
    (type='index') and unioned with --industry. Driven by the UI refresh
    button, so a small selection recomputes in seconds.

  --benchmark CODE[,CODE...] (default 000300):
    Offset benchmark(s) to materialize. benchmark_code is part of the PK,
    so several benchmarks can coexist. Incremental mode runs the missing-
    window detection PER benchmark.

The :class:`CompositesAnalysis` entry class (a :class:`DataAnalysis`
subclass) owns the runtime lifecycle + DB connection lifecycle (opened
by amain() before run(), timed-closed after). Bootstrap-first layout:
the runtime setup runs before this module's imports.
"""
from __future__ import annotations

from typing import Optional, Set

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

from _common.data_analysis import DataAnalysis

from analysis.analysis_composites.config import (
    BASELINE_TABLE,
    DEFAULT_BENCHMARKS,
    TABLE_OFFSETS,
)
from analysis.analysis_composites.opposite_correlations import (
    find_missing_offset_window_ends,
    run_opposite_correlations,
)

logger = setup_logging("analysis_composites")


def _parse_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


class CompositesAnalysis(DataAnalysis):
    """``python -m analysis.analysis_composites`` — opposite-corr offsets."""

    title = ("ANALYZE COMPOSITES — opposite industry correlations by "
             "benchmark offset")
    component = "analysis_composites"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        parser.add_argument(
            "--industry", default="", metavar="ID[,ID...]",
            help="Filtered mode: recompute + upsert ALL windows for the pairs "
                 "among these industry_ids (e.g. BANKS,AI). No truncate.",
        )
        parser.add_argument(
            "--code", default="", metavar="CODE[,CODE...]",
            help="Filtered mode: member index codes (e.g. 000004,000005) "
                 "resolved to industry_ids via stats.sec_classification and "
                 "unioned with --industry.",
        )
        parser.add_argument(
            "--benchmark", default=",".join(DEFAULT_BENCHMARKS),
            metavar="CODE[,CODE...]",
            help="Offset benchmark index code(s) to materialize (default "
                 f"{','.join(DEFAULT_BENCHMARKS)}). Part of the table PK, so "
                 "several benchmarks can coexist.",
        )

    def apply_args(self) -> None:
        self.industry_args = _parse_csv(self.args.industry)
        self.code_args = _parse_csv(self.args.code)
        self.benchmarks = _parse_csv(self.args.benchmark) or list(DEFAULT_BENCHMARKS)
        if self.args.force and (self.industry_args or self.code_args):
            self.parser.error("--force cannot be combined with --industry/--code "
                              "(filtered runs never truncate the table)")

    def header_fields(self) -> dict:
        return {
            "source_table": f"{BASELINE_TABLE} + stats.index_basic_stats",
            "tables": TABLE_OFFSETS,
            "benchmarks": ", ".join(self.benchmarks),
            "mode": "FORCE (full recompute)" if self.args.force
                    else "FILTERED (chosen industries, recompute + upsert)"
                    if (self.industry_args or self.code_args)
                    else "incremental (missing windows only)",
        }

    async def run(self) -> None:
        conn = self.conn

        if self.industry_args or self.code_args:
            # ---- Filtered mode: recompute + upsert chosen industries ----
            industry_ids: Optional[Set[str]] = set(self.industry_args)
            if self.code_args:
                resolved = await self.resolve_codes_to_industries(
                    self.code_args,
                )
                unmapped = sorted(
                    set(self.code_args)
                    - {
                        r["code"] for r in await conn.fetch(
                            "SELECT code FROM stats.sec_classification "
                            "WHERE type = 'index' AND code = ANY($1)",
                            self.code_args,
                        )
                    }
                )
                if unmapped:
                    logger.warning(f"    -> WARNING: codes with no index "
                          f"classification (ignored): "
                          f"{', '.join(unmapped)}")
                industry_ids |= resolved
            logger.info(f"\n[0/1] Filtered mode: {len(industry_ids)} industries "
                  f"({', '.join(sorted(industry_ids))})")
            if len(industry_ids) < 2:
                logger.info("    -> fewer than 2 industries — no pairs to "
                      "compute; nothing to do.")
                return
            await run_opposite_correlations(
                conn, industry_ids=industry_ids, benchmarks=self.benchmarks,
            )
        elif self.args.force:
            await run_opposite_correlations(
                conn, force=True, benchmarks=self.benchmarks,
            )
        else:
            for bench in self.benchmarks:
                logger.info(f"\n[0/1] Detecting missing offset-corr windows for "
                      f"benchmark {bench}...")
                target_dates, load_from = (
                    await find_missing_offset_window_ends(conn, bench)
                )
                logger.info(f"    -> {len(target_dates)} window ends to "
                      f"(re)compute for {TABLE_OFFSETS} (benchmark={bench})"
                      + (f"; bounded load from {load_from.isoformat()}"
                         if load_from else ""))
                if not target_dates:
                    logger.info(f"    -> benchmark {bench} is up to date; "
                          f"nothing to do.")
                    continue
                await run_opposite_correlations(
                    conn, target_dates=target_dates, load_from=load_from,
                    benchmarks=(bench,),
                )


if __name__ == "__main__":
    CompositesAnalysis().execute()
