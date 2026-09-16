"""builds.etf — Build combined SZSE + SSE ETF OHLCV + margin + composition +
PE data and insert directly to the database (missing-data-only).

NOTE: This script loads ONLY ETF data. Index composition (CSI + SZSE
closeweight CSVs) is now loaded by `python -m builds.index.composition`
which writes to the same stats.sec_composition table with
source_type='index'. Run builds.index.composition BEFORE builds.index.baseline
so that index shared weights are available for close-price estimation.

Reads the per-day SZSE/SSE CSV archives produced by download scripts:
  - SZSE: szse_archive/szse_etf_YYYYMMDD.csv        (2022-01 → 2025-06-30 legacy)
  - SZSE: szse_trend/szse_trend_etf_YYYYMMDD.csv    (2025-07 → today snapshot)
  - SSE: sse_trend/sse_trend_etf_YYYYMMDD.csv       (today snapshot, ETF/fund tab)
  - SZSE/SSE margin detail CSVs                     (per-security margin)
  - SZSE: szse_etf_composition/szse_etf_comp_YYYYMMDD_<code>.csv (per-file)

ETF PE: computed via HARMONIC weighting of constituent stock PE from
stats.stock_basic_stats by the LATEST stats.sec_composition snapshot:
    PE_etf = SUM(w_i) / SUM(w_i / PE_i)
Loss-making constituents (NULL PE) are excluded from both numerator and
denominator. PE scope is INCREMENTAL — recomputed only for rows eligible
for re-upsert this run (missing dates ∪ corp-action resync codes ∪ PE-null
keys). Run builds.stock BEFORE builds.etf so stock PE is available.

The full staged pipeline lives in ``builds.etf.pipeline``; this entry
class (:class:`EtfBuild`, a :class:`DataBuild` subclass) owns only the
runtime lifecycle (resource pre-check, cudf.pandas activation, UTF-8
stdout, common --start-date/--end-date/--force/--date/--code args,
post-check memory release). The pipeline module is imported lazily
inside :meth:`EtfBuild.run` so the cudf.pandas import hook is installed
before its pandas import.

Missing-data detection flow (DB-first):
  OHLCV + margin (cross-date dependency — splits + MAs need FULL history):
    discover files → DB scope (force purge / missing dates + recent re-scan)
    → read only needed CSVs + DB history → merge/adjust/MAs → incremental PE
    → filter to write candidates → upsert etf_identity + 4 split tables.
  ETF composition (sec_composition source_type='etf'): missing snapshots only.
  sec_classification type='etf': per-code quality metrics, idempotent upsert
    (classification + index_code columns preserved on conflict).

With --force: truncate stats.etf_identity and DELETE FROM stats.sec_composition
WHERE source_type='etf' (index composition rows preserved), then read ALL
source CSVs (DB empty → full-history scope).

With --date YYYY-MM-DD: single-date forced rebuild — the date is processed
even when already in the DB (existing rows refreshed via the normal upsert
write paths; no truncation, no deletes). Mutually exclusive with --force.

Usage:
  python -m builds.etf
  python -m builds.etf --start-date 2024-01-01 --end-date 2025-06-30
  python -m builds.etf --force
  python -m builds.etf --date 2025-06-27          (force-rebuild this one date)
  python -m builds.etf --code 159919.SZ           (single-ETF test filter)
"""

from _common.data_build import DataBuild



class EtfBuild(DataBuild):
    """``python -m builds.etf`` — runtime lifecycle + common args only.

    Empty ``title``: pipeline.main() prints its own header + wall time.
    """

    component = "etf"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        # --date: reject the --date + --force combo and validate the value
        # BEFORE any work starts (SystemExit 2 on misuse); the pipeline
        # consumes args.forced_date.
        self.apply_date_force_args()
        # Resolve once so pipeline stages share the canonical suffixed code.
        self.args.resolved_code = self.resolve_code_filter()

    async def run(self) -> None:
        from builds.etf.pipeline.main import run

        await run(self.args)


if __name__ == "__main__":
    EtfBuild().execute()
