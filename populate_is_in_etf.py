"""populate_is_in_etf.py — One-time script: add is_in_etf column to stock_identity.

Adds ``is_in_etf BOOLEAN DEFAULT FALSE`` to stats.stock_identity (if absent),
rebuilds the suffix index to INCLUDE is_in_etf, then populates the column by
marking TRUE every (date, code) row whose code appears in sec_composition
with source_type='etf'.

Usage:
    python populate_is_in_etf.py
"""
import time

from _db_commons import get_db_connection


def main() -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Add column if it doesn't exist
    print("Adding is_in_etf column to stats.stock_identity ...", flush=True)
    cur.execute("""
        ALTER TABLE stats.stock_identity
            ADD COLUMN IF NOT EXISTS is_in_etf BOOLEAN NOT NULL DEFAULT FALSE
    """)
    print("  done.", flush=True)

    # 2. Drop and recreate the suffix index to INCLUDE is_in_etf
    print("Rebuilding idx_stock_identity_suffix_code_date with is_in_etf ...", flush=True)
    cur.execute("DROP INDEX IF EXISTS stats.idx_stock_identity_suffix_code_date")
    cur.execute("""
        CREATE INDEX idx_stock_identity_suffix_code_date
            ON stats.stock_identity (code_suffix, code, date DESC)
            INCLUDE (name, is_in_etf)
    """)
    print("  done.", flush=True)

    # 3. Populate: set is_in_etf = TRUE for all rows whose code is in any ETF
    print("Populating is_in_etf from sec_composition (source_type='etf') ...", flush=True)
    t0 = time.time()
    cur.execute("""
        UPDATE stats.stock_identity si
           SET is_in_etf = TRUE
         WHERE si.code IN (
             SELECT DISTINCT sc.stock_code
               FROM stats.sec_composition sc
              WHERE sc.source_type = 'etf'
         )
    """)
    n_updated = cur.rowcount
    print(f"  updated {n_updated:,} rows in {time.time() - t0:.1f}s", flush=True)

    # 4. Vacuum to materialize the new index
    print("VACUUM ANALYZE stats.stock_identity ...", flush=True)
    conn.autocommit = True
    cur.execute("VACUUM ANALYZE stats.stock_identity")
    print("  done.", flush=True)

    # 5. Quick sanity check
    cur.execute("""
        SELECT code_suffix,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE is_in_etf) AS in_etf
          FROM stats.stock_identity
         GROUP BY code_suffix
         ORDER BY code_suffix
    """)
    print("\nSummary by exchange:", flush=True)
    print(f"  {'suffix':<8} {'total':>10} {'in_etf':>10} {'pct':>8}", flush=True)
    for row in cur.fetchall():
        suffix, total, in_etf = row
        pct = in_etf * 100.0 / total if total else 0
        print(f"  {suffix or '(null)':<8} {total:>10,} {in_etf:>10,} {pct:>7.1f}%", flush=True)

    cur.close()
    conn.close()
    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
