"""SSE index flow — index endpoint → sse_index_intraday CSV → index_intraday_5min.

Polls the SSE list ``exchange/index`` JSONP endpoint (same schema as the
equity tab, only the path suffix differs) for all Shanghai-listed indices,
archives raw snapshots to ``temps/sse_index_intraday/sse_index_intraday_YYYYMMDD.csv``
and aggregates 5 one-minute samples into OHLC bars for
``stats.index_intraday_5min`` (FK parent ``stats.index_identity``).

Index bars carry NO ``trading_shares`` / ``code_suffix`` columns (the
``index_intraday_5min`` schema has no volume field) and store the code BARE
(e.g. ``000001``), matching ``index_identity``'s CHECK constraint.

Snapshots are filtered to indices that already exist in
``stats.index_identity`` — SSE publishes ~200 indices; only the subset we
already have daily history for is streamed.
"""
from __future__ import annotations

from downloads._common.core import setup_logger
from downloads.stock.sse._common.list_endpoint import SSE_INDEX_LIST_URL

from ._io import _prepopulate_finished_codes
from ._model import AssetStream

logger = setup_logger("stream_sse")


def build_index_asset(allowed_codes: set) -> AssetStream:
    """Construct the AssetStream for the SSE index (指数) flow.

    ``allowed_codes`` is the set of bare 6-digit index codes already tracked
    in ``stats.index_identity``. Snapshot rows whose code is NOT in this set
    are dropped before entering the buffer.
    """
    return AssetStream(
        name="index",
        list_url=SSE_INDEX_LIST_URL,
        identity_table="stats.index_identity",
        intraday_table="stats.index_intraday_5min",
        code_suffix=None,
        has_volume=False,
        allowed_codes=allowed_codes,
        csv_subdir="sse_index_intraday",
        csv_prefix="sse_index_intraday",
    )


def prepopulate_index_finished_codes(conn, trade_date, finished_codes: set) -> None:
    """Pre-populate finished_codes with SSE indices that already have a 15:00 bar."""
    _prepopulate_finished_codes(
        conn, trade_date, finished_codes,
        table="stats.index_intraday_5min", code_suffix_filter=None,
    )


def load_existing_index_codes(conn) -> set:
    """Return the set of bare 6-digit index codes already tracked in
    stats.index_identity.

    SSE index snapshots are filtered to this set so we only stream intraday
    bars for indices we already have daily history for (SSE publishes ~200
    indices; the rest are skipped). Returns an empty set on error (which
    would effectively disable index streaming).
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT code FROM stats.index_identity")
            return {r[0] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load existing index codes from index_identity: %s", e)
        return set()


def sync_index_has_intraday_flag(conn, trade_date) -> int:
    """Mark stats.index_basic_stats.has_intraday_5mins = TRUE for every
    (date, code) that now has a bar in stats.index_intraday_5min for
    trade_date. Mirrors build_csindex.sync_has_intraday_flag but scoped to a
    single date (the streaming service only ever touches today's bars).
    """
    sql = """
        UPDATE stats.index_basic_stats bs
           SET has_intraday_5mins = TRUE
          FROM (SELECT DISTINCT code FROM stats.index_intraday_5min
                 WHERE date = %s) sub
         WHERE bs.date = %s AND bs.code = sub.code
           AND bs.has_intraday_5mins IS NOT TRUE
    """
    n = 0
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (trade_date, trade_date))
            n = cur.rowcount
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to sync index has_intraday_5mins flag: %s", e)
    return n
