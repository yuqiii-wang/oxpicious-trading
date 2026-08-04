"""One-off script: run ONLY the correlations step of analyze.industry_sentiments
with force=True (full recompute), reusing the already-populated
analysis.industry_sentiments table.

This avoids needlessly recomputing industry_sentiments (351K rows, already
populated) and industry_attributions (2.6M rows depending on 43M
sec_alloc_perf_attribution rows).

The correlations step loads mean_price from analysis.industry_sentiments and
populates analysis.industry_correlations with pairwise rolling Pearson
correlations. See analyze/industry_sentiments/correlations.py for details.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load database/.env into os.environ
env_path = Path("database/.env")
for line in env_path.read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from utils.build_commons import get_db_connection_async, setup_utf8_stdout  # noqa: E402
from analyze.industry_sentiments.correlations import run_correlations  # noqa: E402

setup_utf8_stdout()


async def main() -> None:
    conn = await get_db_connection_async()
    try:
        await run_correlations(conn, target_dates=None, force=True)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
