"""gating — the scheduled-download cadence gates.

Shared by the services that self-gate their scheduled runs off
``monitor.service_audit`` (the SSE stock PE anchor crawl's weekly gate,
the CSIndex index-PE weekly gate): the gate clock is the service's own
last NORMAL exit, so a renamed service starts fresh (first run proceeds
by design) and a failed run never advances the cadence.
"""
from __future__ import annotations

from typing import Optional

from _common.db_commons import get_db_connection


def days_since_last_normal_exit(service: str) -> Optional[float]:
    """Calendar days since the service's last NORMAL exit in
    monitor.service_audit — the cadence gate's clock. None = never ran
    (the current run's still-open audit row is excluded by the
    ``ended_at IS NOT NULL`` filter). Opens its own connection: the
    gates run at the very start of a scheduled pass, before any shared
    connection exists."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(epoch FROM (now() - MAX(ended_at))) / 86400.0 "
                "AS days FROM monitor.service_audit "
                "WHERE service = %s AND is_normal_exit "
                "AND ended_at IS NOT NULL",
                (service,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        return None
    return float(row[0])
