import asyncio, asyncpg, os
from pathlib import Path
for line in Path("database/.env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

async def main():
    conn = await asyncpg.connect(host=os.environ["SUPABASE_HOST"], port=int(os.environ["SUPABASE_PORT"]), database=os.environ["SUPABASE_DB"], user=os.environ["SUPABASE_USER"], password=os.environ["SUPABASE_PASSWORD"])
    n = await conn.fetchval("SELECT COUNT(*) FROM analysis.industry_correlations")
    print(f"industry_correlations: {n:,} rows")
    # sample a couple of well-known industry pairs
    rows = await conn.fetch("""
        SELECT industry_id, benchmark_industry_id, pool_size, date,
               industry_mean_corr_60d
        FROM analysis.industry_correlations
        WHERE pool_size = 'all'
        ORDER BY date DESC, industry_id, benchmark_industry_id
        LIMIT 5
    """)
    for r in rows:
        print(f"  {r['industry_id']:20s} <-> {r['benchmark_industry_id']:20s} pool={r['pool_size']:5s} {r['date']} corr60d={r['industry_mean_corr_60d']}")
    await conn.close()

asyncio.run(main())
