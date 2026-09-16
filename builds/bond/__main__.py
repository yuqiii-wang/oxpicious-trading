"""builds/bond/__main__.py — Build debt-market baseline and insert directly to
the database (no intermediate CSV).

Aggregates daily-frequency data sources into the 8 debt_* tables:
  1. PBoC OMO daily reverse-repo announcements     → stats.debt_omo
  2. PBoC outright-repo tender announcements        → stats.debt_outright_repo
  3. PBoC MLF tender                                → stats.debt_mlf
  4. Repo lifecycle tracking (running cumulative)   → stats.debt_repo
  5. SHIBOR daily fixing rates                      → stats.debt_shibor
  6. China bond (中债国债) daily yield-curve data    → stats.debt_treasury
  7. PBoC LPR monthly announcements                 → stats.debt_lpr
  8. PBoC Open Market Announcements policy notices  → stats.pboc_oma

Usage:
  python -m builds.bond
  python -m builds.bond --start-date 2024-01-01 --end-date 2026-07-14
  python -m builds.bond --date 2026-07-14          (force single-date rebuild)
  python -m builds.bond --force

See builds/bond/pipeline.py for the full missing-data detection flow.

The entry class delegates to builds.bond.pipeline; the runtime lifecycle
(resource pre-check, cudf.pandas activation, UTF-8 stdout, post-check
memory release) is owned by the :class:`DataBuild` ABC, the header and
wall time by the pipeline itself (empty ``title``). The pipeline module
is imported lazily inside :meth:`BondBuild.run` (it imports pandas —
the cudf.pandas hook must be installed first).
"""

from _common.data_build import DataBuild


class BondBuild(DataBuild):
    """``python -m builds.bond`` — delegates to builds.bond.pipeline."""

    allow_unknown_args = True  # pipeline parses sys.argv itself

    async def run(self) -> None:
        from builds.bond.pipeline import main

        await main()


if __name__ == "__main__":
    BondBuild().execute()
