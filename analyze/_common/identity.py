"""Upsert into analysis.analysis_identity.

The identical INSERT...ON CONFLICT DO UPDATE SQL was duplicated in 6
places across the analyze scripts (mov_ave_spread/__main__,
industry_sentiments/__main__, correlations, attributions,
etf_contribution, sec_alloc_perf_attribution/__main__). Consolidated
here as a single async function.
"""
from __future__ import annotations


async def upsert_analysis_identity(
    conn,
    name: str,
    detail_name: str,
    description: str,
    *,
    summary_name: str | None = None,
) -> None:
    """Upsert a row into analysis.analysis_identity.

    Args:
        conn: asyncpg connection.
        name: analysis name (PK). E.g. "mov_ave_spread".
        detail_name: detail table name. E.g. "mov_ave_spreads_detail".
        description: human-readable description of the analysis.
        summary_name: optional summary table name. None for analyses
            without a separate summary table.
    """
    await conn.execute(
        """
        INSERT INTO analysis.analysis_identity
            (name, detail_name, summary_name, last_run_datetime, description)
        VALUES ($1, $2, $3, NOW(), $4)
        ON CONFLICT (name) DO UPDATE SET
            detail_name       = EXCLUDED.detail_name,
            summary_name      = EXCLUDED.summary_name,
            last_run_datetime = NOW(),
            description       = EXCLUDED.description
        """,
        name,
        detail_name,
        summary_name,
        description,
    )
    print(f"    -> upserted analysis_identity (name='{name}')", flush=True)
