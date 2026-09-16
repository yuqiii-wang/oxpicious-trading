"""
builds.index.composition — Build CSI + SZSE index composition snapshots
and insert directly to stats.sec_composition (missing-data-only, no
intermediate CSV).

Reads the per-index closeweight CSVs produced by download scripts:
  • CSI:  temps/csi_index_composition/*_closeweight_*.csv
  • SZSE: temps/szse_index_composition/*_closeweight_*.csv

Each CSV contains one snapshot_date for one index_code with columns
(snapshot_date, index_code, stock_code, stock_name, weight_pct). Rows
are mapped to stats.sec_composition with source_type='index', ranked by
weight descending within each (code, snapshot_date) group.

Missing-data detection flow (DB-first):
  1. Query stats.sec_composition for existing (code, snapshot_date) pairs
     where source_type='index'
  2. Read all composition CSVs into rows
  3. Filter to missing (code, snapshot_date) pairs
  4. Bulk upsert only the missing rows

With --force: DELETE FROM stats.sec_composition WHERE source_type='index'
first (ETF composition rows are preserved — they are owned by
builds.etf). Then read ALL source CSVs and insert.

With --date YYYY-MM-DD: single-snapshot-date rebuild — only composition
CSVs whose snapshot date matches are read and the missing-pair skip is
bypassed, so rows already in the DB are refreshed through the upsert
path (no DELETE, no truncation). Mutually exclusive with --force.

Usage:
  python -m builds.index.composition
  python -m builds.index.composition --force
  python -m builds.index.composition --date 2026-08-14   (force one snapshot date)

The :class:`IndexCompositionBuild` entry class (a :class:`DataBuild`
subclass) owns the runtime lifecycle + common argparse. Bootstrap-first
layout: the runtime setup runs before this module's imports.
"""

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import time

from _common.build_commons import (
    print_build_header, print_wall_time, TODAY_STR,
)
from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR
from _common.log_setup import setup_logging

from _common.data_build import DataBuild

logger = setup_logging("composition")


class IndexCompositionBuild(DataBuild):
    """``python -m builds.index.composition`` — CSI+SZSE snapshots → DB."""

    title = ("BUILD INDEX COMPOSITION (CSI + SZSE)  ·  "
             "missing-data-only → stats.sec_composition")
    component = "composition"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)

    def apply_args(self) -> None:
        # --date mode: mutual exclusion + parse (SystemExit 2 on bad input),
        # then validate the forced snapshot date against the composition CSV
        # filenames (CSI + SZSE union) before any DB work. exits(1) when the
        # date has no source snapshot.
        from _common.build_commons import forced_date_scope

        self.forced_date = self.apply_date_force_args()
        if self.forced_date is not None:
            from builds.index.composition import available_snapshot_dates

            logger.info(f"[DATE MODE] Forced single-date build: {self.forced_date}")
            forced_date_scope(
                available_snapshot_dates(),
                self.forced_date,
                source_label="composition CSV filenames",
            )

    def header_fields(self) -> dict:
        return {
            "CSI comp dir":  INDEX_COMP_DIR,
            "SZSE comp dir": SZSE_INDEX_COMP_DIR,
            "Forced date":   str(self.forced_date) if self.forced_date else "(none)",
            "Today":         TODAY_STR,
        }

    async def run(self) -> None:
        from _common.build_commons import (
            copy_or_upsert_split_async, forced_date_scope,
        )
        from builds.index.composition import (
            build_index_composition_rows,
            build_szse_index_composition_rows,
        )

        # ------------------------------------------------------------------
        # (1) Connect to DB and find existing (code, snapshot_date) pairs
        # ------------------------------------------------------------------
        logger.info("\n[1/4] Connecting to database and detecting missing snapshots …")
        conn = await self.connect_db()

        try:
            if self.args.force:
                logger.info("    [DB] Force mode: deleting existing index composition rows "
                      "(source_type='index', ETF rows preserved)")
                await conn.execute(
                    "DELETE FROM stats.sec_composition WHERE source_type = 'index'"
                )
                existing_comp_keys = set()
            else:
                comp_existing_rows = await conn.fetch(
                    "SELECT DISTINCT code, snapshot_date "
                    "FROM stats.sec_composition WHERE source_type = 'index'"
                )
                existing_comp_keys = {
                    (r["code"], r["snapshot_date"]) for r in comp_existing_rows
                }
                logger.info(f"    [DB] {len(existing_comp_keys):,} existing (code, snapshot_date) pairs "
                      f"in stats.sec_composition (source_type='index')")

            # ------------------------------------------------------------------
            # (2) Build composition rows from CSI + SZSE CSVs
            # ------------------------------------------------------------------
            logger.info("\n[2/4] Building CSI index composition rows …")
            index_comp_rows = await build_index_composition_rows(
                conn=conn, force=self.args.force, forced_date=self.forced_date)

            logger.info("\n[3/4] Building SZSE index composition rows …")
            szse_index_comp_rows = await build_szse_index_composition_rows(
                conn=conn, force=self.args.force, forced_date=self.forced_date)

            all_rows = index_comp_rows + szse_index_comp_rows
            logger.info(f"\n    → total: {len(all_rows):,} index composition rows "
                  f"({len(index_comp_rows):,} CSI + {len(szse_index_comp_rows):,} SZSE)")

            # ------------------------------------------------------------------
            # (3) Filter to missing (code, snapshot_date) pairs and insert
            # ------------------------------------------------------------------
            logger.info("\n[4/4] Filtering to missing pairs and inserting …")
            # --date mode bypasses the missing-pair filter: every row built from
            # the forced snapshot date is (re)written via the upsert path.
            if not self.args.force and existing_comp_keys and not self.forced_date:
                n_before = len(all_rows)
                all_rows = [
                    r for r in all_rows
                    if (r["code"], r["snapshot_date"]) not in existing_comp_keys
                ]
                n_skipped = n_before - len(all_rows)
                logger.info(f"    [DB] {len(all_rows):,} rows to insert "
                      f"(skipped {n_skipped:,} existing)")
            else:
                logger.info(f"    [DB] {len(all_rows):,} rows to insert")

            if all_rows:
                n_copied, n_upserted = await copy_or_upsert_split_async(
                    conn, "stats.sec_composition", all_rows,
                    ["code", "snapshot_date", "rank"],
                    date_column="snapshot_date",
                )
                total = n_copied + n_upserted
                via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                      f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                      "upsert"
                logger.info(f"    [DB] Inserted {total:,} rows into stats.sec_composition via {via}")
            else:
                logger.info(f"    [DB] No new rows to insert into stats.sec_composition")

        finally:
            await conn.close()


if __name__ == "__main__":
    IndexCompositionBuild().execute()
