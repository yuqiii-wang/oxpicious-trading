"""Search by forecast_id (analyze.analysis_forecasts.fetch.identity).

Resolves one forecast_id against analysis_forecasts.forecast_identities
— the shared-PK registry: one row per bucket holding the identity
columns every motivation table's forecast_id links back to.
"""
from __future__ import annotations

import json

from analyze.analysis_forecasts.config import (
    IDENTITY_BUCKET_TABLES,
    TABLE_IDENTITIES,
)


async def fetch_forecast_identity(
    conn,
    forecast_id: int,
) -> dict | None:
    """Resolve one forecast_id against the identities registry.

    Returns the identity row (forecast_id, sec_type, code, stat_month,
    bucket, streak_signal_days, delayed_signal_days, lookback_period —
    code = the forecast subject: the security ticker, or the DROPPING
    industry_id for opp_pair rows) plus, when the bucket's motivation
    table is one of the known families, the full motivation row joined
    from it under the ``motivation`` key (dates arrive as ISO strings
    via the to_jsonb cast). None when the id is not registered.

    Backs ``python -m analyze.analysis_forecasts --search-forecast-id``
    (the bucket table name is validated against the config's known set
    before it is ever interpolated into SQL).
    """
    row = await conn.fetchrow(
        f"""
        SELECT forecast_id, sec_type, code, stat_month, bucket,
               streak_signal_days, delayed_signal_days, lookback_period
        FROM {TABLE_IDENTITIES}
        WHERE forecast_id = $1
        """,
        forecast_id,
    )
    if row is None:
        return None
    ident = dict(row)
    table = IDENTITY_BUCKET_TABLES.get(ident["bucket"])
    if table is not None:
        motivation = await conn.fetchrow(
            f"""
            SELECT to_jsonb(m) AS motivation
            FROM {table} m
            WHERE m.forecast_id = $1
            """,
            forecast_id,
        )
        # asyncpg hands JSONB back as a str — decode to a dict.
        ident["motivation"] = (
            json.loads(motivation["motivation"])
            if motivation is not None else None
        )
    return ident
