"""Quick DB volume checker for the cuDF refactor planning.

Connects to the local Supabase DB (env vars from database/.env), counts
rows in every table that feeds the build/analyze pipelines, and prints a
summary so the cuDF router thresholds can be validated against the real
workload.

Run from WSL: python3 -m temp_scripts._check_db_volumes
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load database/.env into os.environ (same logic as _common.db_commons).
_env_path = Path(__file__).resolve().parents[1] / "database" / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import asyncpg  # noqa: E402


_QUERIES = [
    # Build source tables (the inputs to compute_moving_averages etc.)
    ("stats.stock_basic_stats",       "SELECT COUNT(*) FROM stats.stock_basic_stats"),
    ("stats.stock_identity",          "SELECT COUNT(*) FROM stats.stock_identity"),
    ("stats.etf_basic_stats",         "SELECT COUNT(*) FROM stats.etf_basic_stats"),
    ("stats.etf_identity",            "SELECT COUNT(*) FROM stats.etf_identity"),
    ("stats.index_basic_stats",       "SELECT COUNT(*) FROM stats.index_basic_stats"),
    ("stats.index_identity",          "SELECT COUNT(*) FROM stats.index_identity"),
    ("stats.stock_tech_stats",        "SELECT COUNT(*) FROM stats.stock_tech_stats"),
    ("stats.etf_tech_stats",          "SELECT COUNT(*) FROM stats.etf_tech_stats"),
    ("stats.index_tech_stats",        "SELECT COUNT(*) FROM stats.index_tech_stats"),
    # Analyze output tables (the workloads most likely to benefit from cuDF)
    ("analysis.mov_ave_spreads_detail",
     "SELECT COUNT(*) FROM analysis.mov_ave_spreads_detail"),
    ("analysis.mov_ave_peaks_and_floors",
     "SELECT COUNT(*) FROM analysis.mov_ave_peaks_and_floors"),
    ("analysis.mov_ave_rsi",
     "SELECT COUNT(*) FROM analysis.mov_ave_rsi"),
    ("analysis.sec_alloc_perf_attribution",
     "SELECT COUNT(*) FROM analysis.sec_alloc_perf_attribution"),
    ("analysis.industry_sentiments",
     "SELECT COUNT(*) FROM analysis.industry_sentiments"),
    ("analysis.industry_correlations",
     "SELECT COUNT(*) FROM analysis.industry_correlations"),
    ("analysis.industry_etf_contribution",
     "SELECT COUNT(*) FROM analysis.industry_etf_contribution"),
    ("analysis.industry_attributions",
     "SELECT COUNT(*) FROM analysis.industry_attributions"),
]

# Per-(table, sec_type) counts where the table has a sec_type column —
# lets us see which sec_type dominates the workload.
_BREAKDOWN = [
    ("analysis.mov_ave_spreads_detail by sec_type",
     "SELECT sec_type, COUNT(*) FROM analysis.mov_ave_spreads_detail GROUP BY sec_type ORDER BY sec_type"),
    ("analysis.mov_ave_rsi by sec_type",
     "SELECT sec_type, COUNT(*) FROM analysis.mov_ave_rsi GROUP BY sec_type ORDER BY sec_type"),
]


async def main() -> int:
    conn = await asyncpg.connect(
        host=os.environ.get("SUPABASE_HOST", "127.0.0.1"),
        port=int(os.environ.get("SUPABASE_PORT", "9876")),
        database=os.environ.get("SUPABASE_DB", "oxpicious-stats"),
        user=os.environ.get("SUPABASE_USER", "postgres"),
        password=os.environ.get("SUPABASE_PASSWORD", "postgres"),
        timeout=15,
    )
    try:
        print(f"\n  DB: {os.environ.get('SUPABASE_DB', 'oxpicious-stats')} "
              f"@ {os.environ.get('SUPABASE_HOST', '127.0.0.1')}:"
              f"{os.environ.get('SUPABASE_PORT', '9876')}\n")
        print(f"  {'Table':<48s}{'Rows':>14s}")
        print(f"  {'-' * 48}{'-' * 14}")
        for label, sql in _QUERIES:
            try:
                n = await conn.fetchval(sql)
            except Exception as e:
                print(f"  {label:<48s}{type(e).__name__}: {e}"[:120])
                continue
            print(f"  {label:<48s}{int(n or 0):>14,}")
        print()
        for label, sql in _BREAKDOWN:
            try:
                rows = await conn.fetch(sql)
            except Exception as e:
                print(f"  {label}: {type(e).__name__}: {e}"[:120])
                continue
            print(f"  {label}")
            for r in rows:
                print(f"    {r[0]:<10s}{int(r[1]):>14,}")
            print()
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
