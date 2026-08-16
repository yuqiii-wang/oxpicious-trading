"""database/sql/stats/migrate_options_terms.py — Add underlying_target_type and exchange columns to options_terms.

This script:
1. Adds the `underlying_target_type` and `exchange` columns to stats.options_terms
2. Updates the CHECK constraint on exchange to include 'CFFEX'
3. Backfills existing data: SZSE ETF options → (ETF, SZSE), CFFEX index options → (INDEX, CFFEX)

Usage:
  python -m database.sql.stats.migrate_options_terms
"""
from __future__ import annotations

import asyncio
import sys

from _common.build_commons import setup_utf8_stdout, get_db_or_exit

setup_utf8_stdout()


async def migrate() -> None:
    conn = await get_db_or_exit()
    try:
        # -- 1. Add underlying_target_type column if not exists --
        col_check = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'stats'
              AND table_name = 'options_terms'
              AND column_name = 'underlying_target_type'
            """
        )
        if not col_check:
            print("[MIGRATE] Adding underlying_target_type column …", flush=True)
            await conn.execute(
                """
                ALTER TABLE stats.options_terms
                ADD COLUMN underlying_target_type TEXT
                    CHECK (underlying_target_type IN ('ETF','INDEX'))
                """
            )
            print("    Done.", flush=True)
        else:
            print("[MIGRATE] underlying_target_type column already exists.", flush=True)

        # -- 2. Add exchange column if not exists --
        col_check = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'stats'
              AND table_name = 'options_terms'
              AND column_name = 'exchange'
            """
        )
        if not col_check:
            print("[MIGRATE] Adding exchange column …", flush=True)
            await conn.execute(
                """
                ALTER TABLE stats.options_terms
                ADD COLUMN exchange TEXT
                    CHECK (exchange IN ('SZSE','SSE','CFFEX'))
                """
            )
            print("    Done.", flush=True)
        else:
            print("[MIGRATE] exchange column already exists.", flush=True)
            # Update the CHECK constraint to include CFFEX
            print("[MIGRATE] Updating exchange CHECK constraint to include 'CFFEX' …", flush=True)
            await conn.execute(
                """
                ALTER TABLE stats.options_terms
                DROP CONSTRAINT IF EXISTS options_terms_exchange_check
                """
            )
            await conn.execute(
                """
                ALTER TABLE stats.options_terms
                ADD CONSTRAINT options_terms_exchange_check
                CHECK (exchange IN ('SZSE','SSE','CFFEX'))
                """
            )
            print("    Done.", flush=True)

        # -- 3. Backfill existing data --
        print("[MIGRATE] Backfilling underlying_target_type and exchange for existing data …", flush=True)

        # CFFEX index options: contract_code starts with IO/HO/MO/CO
        cffex_prefixes = ["IO%", "HO%", "MO%", "CO%"]
        conditions = " OR ".join(
            [f"contract_code LIKE ${i+1}" for i in range(len(cffex_prefixes))]
        )

        # Update CFFEX options (no JOIN needed — contract_code lives in options_terms)
        cffex_count = await conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM stats.options_terms
            WHERE ({conditions})
              AND (underlying_target_type IS NULL OR exchange IS NULL)
            """,
            *cffex_prefixes,
        )
        print(f"    CFFEX options needing backfill: {cffex_count}", flush=True)

        if cffex_count and cffex_count > 0:
            await conn.execute(
                f"""
                UPDATE stats.options_terms
                SET underlying_target_type = 'INDEX',
                    exchange = 'CFFEX'
                WHERE ({conditions})
                  AND (underlying_target_type IS NULL OR exchange IS NULL)
                """,
                *cffex_prefixes,
            )
            print(f"    Updated {cffex_count} CFFEX options → (INDEX, CFFEX)", flush=True)

        # SZSE ETF options: everything that's NOT CFFEX and has NULL values
        szse_count = await conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM stats.options_terms
            WHERE NOT ({conditions})
              AND (underlying_target_type IS NULL OR exchange IS NULL)
            """,
            *cffex_prefixes,
        )
        print(f"    SZSE options needing backfill: {szse_count}", flush=True)

        if szse_count and szse_count > 0:
            await conn.execute(
                f"""
                UPDATE stats.options_terms
                SET underlying_target_type = 'ETF',
                    exchange = 'SZSE'
                WHERE NOT ({conditions})
                  AND (underlying_target_type IS NULL OR exchange IS NULL)
                """,
                *cffex_prefixes,
            )
            print(f"    Updated {szse_count} SZSE options → (ETF, SZSE)", flush=True)

        # -- 4. Verify --
        print("\n[VERIFY] Checking final distribution …", flush=True)
        dist = await conn.fetch(
            """
            SELECT exchange, underlying_target_type, COUNT(*) as cnt
            FROM stats.options_terms
            GROUP BY exchange, underlying_target_type
            ORDER BY exchange, underlying_target_type
            """
        )
        for row in dist:
            print(f"    exchange={row['exchange']}, target_type={row['underlying_target_type']}: {row['cnt']} rows", flush=True)

        print("\n[MIGRATE] Done!", flush=True)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
