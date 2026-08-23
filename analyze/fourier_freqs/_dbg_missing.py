"""Debug 3: which range_days does 000300 have for last_date 08-17/08-18,
and do other codes have 750d rows there? Isolates whether the gap is
code-specific or window-specific."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from _common.df_utils._activate import activate
activate()

import psycopg2

conn = psycopg2.connect(host="127.0.0.1", port=9876, dbname="oxpicious-stats",
                        user="postgres", password="postgres")
cur = conn.cursor()

cur.execute("""
    SELECT last_date, range_days, amplitude_spectrum IS NULL AS null_spec
    FROM analysis.fourier_freqs
    WHERE code = '000300' AND sec_type = 'index' AND last_date >= '2026-08-13'
    ORDER BY last_date, range_days
""")
print("000300 rows since 08-13 (last_date, range_days, null_spec):")
for r in cur.fetchall():
    print("  ", r)

cur.execute("""
    SELECT range_days, COUNT(*) FROM analysis.fourier_freqs
    WHERE sec_type = 'index' AND last_date = '2026-08-18'
    GROUP BY range_days ORDER BY range_days
""")
print("\nrows per range_days on 2026-08-18 (all codes):")
for r in cur.fetchall():
    print("  ", r)

cur.execute("""
    SELECT COUNT(*) FROM stats.index_basic_stats
    WHERE code = '000300' AND close IS NOT NULL
      AND date BETWEEN '2026-08-15' AND '2026-08-18'
""")
print("\n000300 close rows 08-15..18:", cur.fetchone()[0])

cur.execute("""
    SELECT date FROM stats.index_basic_stats
    WHERE code = '000300' AND close IS NOT NULL AND date >= '2026-08-12'
    ORDER BY date
""")
print("000300 recent close dates:", [r[0].isoformat() for r in cur.fetchall()])

cur.close()
conn.close()
