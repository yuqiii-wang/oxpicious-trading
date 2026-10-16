"""Importance-rank code ordering — rank 0 (most important) first.

The rank map lives in ``stats.sec_importance_ranks`` (one row per code,
``importance_rank`` 0-9 WITHIN each sec_type; 0 = most important), rebuilt
monthly by ``builds.classification.importance``. Downloads, builds and
analysis enumerate codes through these helpers so per-code work covers the
most important securities first when a run is throttled, interrupted, or
sliced (``--max-funds``, partial sweeps).

Ordering semantics:
  - codes absent from the table default to rank 9 and tie-break by code
    ascending, so ordering is deterministic whether or not the table is
    populated yet (fresh install, first run before the monthly build);
  - within a rank, codes are processed in ascending code order;
  - the scale is PER sec_type — never compare ranks across types. Callers
    order one type's list at a time (the pipelines are per-type loops).

Whole-frame analysis fetches keep their SQL ``ORDER BY code, date`` (the
frame compute is order-invariant); these helpers order the CODE LIST that
enters ``code = ANY($1)`` binds, logs, and any future batching/slicing.
Queries that rank inside SQL itself (``ORDER BY r.importance_rank NULLS
LAST`` joins, e.g. stock/sse/archive) never materialize a Python list and
keep their ordering in the query.

The ``*_or_empty`` / ``*_or_sorted`` variants are the download-loop
contract: ordering must never fail a run. ``fetch_importance_ranks_or_empty``
is for callers that already hold a connection (streamers) — it returns {}
on any failure so the ordering helpers fall back to plain code order.

DOWNLOAD/DB DECOUPLING (2026-10-13): the monthly build also persists the
final rank map AND the per-code exchange map (the download-service
routing key) to ``temps/importance/sec_importance_ranks.json``
(:data:`RANKS_CACHE_FILE`), and :func:`order_codes_or_sorted` /
:func:`exchange_map_or_empty` read that file FIRST — the daily download
tier never needs the DB, so a DB outage during downloads can neither fail
the CSV sweep nor silently degrade the important-codes-first ordering.
The DB read remains only as the fresh-install fallback (first run before
any monthly build).

DOWNLOAD-SERVICE ROUTING BY EXCHANGE (2026-10-13): every ranked code
carries an ``exchange`` (mirroring ``sec_classification.exchange``:
SS / SZ / GEM / STAR / BJ / HK / OVERSEAS, NULL for DUMMY indices) plus
the hand-curated marker ``SZSE`` = SZSE-exchange-PUBLISHED index that
csindex.com.cn does not serve — the SZSE downloader/streamer owns those
codes (:data:`SZSE_DOWNLOADER_EXCHANGE`,
:func:`owned_by_szse_downloader`). Download services that enumerate
codes filter their lists by this map instead of hardcoded code sets.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

RANK_TABLE = "stats.sec_importance_ranks"
DEFAULT_RANK = 9

_RANK_SQL = f"SELECT code, importance_rank FROM {RANK_TABLE}"
_EXCHANGE_SQL = (
    f"SELECT code, exchange FROM {RANK_TABLE} WHERE exchange IS NOT NULL"
)

# Download-service ownership marker. sec_classification never emits this
# value — only the hand-curated importance_rules JSONs can (currently the
# 399001 深证成指 family): an SZSE-exchange-PUBLISHED index that
# csindex.com.cn does not serve. Codes carrying it belong to the SZSE
# downloader/streamer; every csindex service skips them.
SZSE_DOWNLOADER_EXCHANGE = "SZSE"

# File cache of the rank map — the DECOUPLING point between the monthly
# rank build and the daily download tier. Downloads read this file FIRST
# (order_codes_or_sorted) so a DB outage during the download tier can
# neither fail the sweep nor silently degrade code ordering
# (important-codes-first is what makes interrupted/throttled sweeps
# still cover the critical universe). Ordering-only data — no expiry
# check; the monthly build (builds.classification.importance) rewrites
# it right after the DB upsert.
_REPO_ROOT = Path(__file__).resolve().parents[1]
RANKS_CACHE_FILE = (
    _REPO_ROOT / "temps" / "importance" / "sec_importance_ranks.json"
)


def save_importance_ranks_cache(
    ranks: Dict[str, int], exchanges: Dict[str, str] | None = None,
) -> None:
    """Persist the rank + exchange maps to ``RANKS_CACHE_FILE`` (best effort).

    Called by the monthly importance build with the FINAL post-overlay
    rows it just upserted, so downloads and the DB agree on ordering and
    on the exchange download-service routing key. The exchanges map is
    optional for backwards compatibility with older callers.
    """
    try:
        RANKS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RANKS_CACHE_FILE.write_text(
            json.dumps({
                "as_of": _dt.datetime.now().isoformat(timespec="seconds"),
                "ranks": ranks,
                "exchanges": exchanges or {},
            }),
            encoding="utf-8",
        )
        logger.info(
            "importance rank cache saved: %d codes, %d exchanges -> %s",
            len(ranks), len(exchanges or {}), RANKS_CACHE_FILE,
        )
    except Exception as exc:  # noqa: BLE001 — cache is an optimization
        logger.warning("importance rank cache write failed: %s", exc)


def _load_cache_data() -> Dict[str, Any]:
    """Read the cache file's parsed JSON; ``{}`` when missing/corrupt."""
    try:
        return json.loads(RANKS_CACHE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — unreadable cache = no cache
        logger.warning("importance rank cache unreadable (%s); ignoring", exc)
        return {}


def load_importance_ranks_cache() -> Dict[str, int]:
    """Read the rank cache; ``{}`` when missing/corrupt (callers fall
    through to the DB or plain sort)."""
    data = _load_cache_data()
    return {str(c): int(r) for c, r in data.get("ranks", {}).items()}


def load_exchange_map_cache() -> Dict[str, str]:
    """Read the per-code exchange map from the rank cache; ``{}`` when
    the cache predates the exchange column (callers fall through to the
    DB or to their legacy hardcoded routing)."""
    data = _load_cache_data()
    return {str(c): str(e) for c, e in data.get("exchanges", {}).items()}


def order_codes_by_importance(
    codes: Iterable[str], ranks: Dict[str, int]
) -> List[str]:
    """Order codes by (importance_rank, code); unknown codes rank 9."""
    return sorted(codes, key=lambda c: (ranks.get(c, DEFAULT_RANK), c))


def order_pairs_by_importance(
    pairs: Iterable[Tuple[str, Any]], ranks: Dict[str, int]
) -> List[Tuple[str, Any]]:
    """Order ``(code, payload)`` pairs by (importance_rank, code); unknown 9.

    Same ordering as :func:`order_codes_by_importance` for lists that carry
    per-code data (names, etc.) — payloads ride along untouched.
    """
    return sorted(pairs, key=lambda p: (ranks.get(p[0], DEFAULT_RANK), p[0]))


def fetch_importance_ranks(conn) -> Dict[str, int]:
    """Sync (psycopg) rank map {code: importance_rank} from the stats table."""
    with conn.cursor() as cur:
        cur.execute(_RANK_SQL)
        return {code: int(rank) for code, rank in cur.fetchall()}


def fetch_importance_ranks_or_empty(conn) -> Dict[str, int]:
    """Sync rank map over a caller-held connection; {} on any failure.

    For download loops that read through their long-lived connection and
    must never fail a run on ordering — an empty map makes the ordering
    helpers fall back to plain code order.
    """
    try:
        return fetch_importance_ranks(conn)
    except Exception as exc:  # noqa: BLE001 — ordering must never fail a run
        logger.warning("importance ranks unavailable: %s", exc)
        return {}


async def fetch_importance_ranks_async(conn) -> Dict[str, int]:
    """Async (asyncpg) rank map {code: importance_rank}."""
    rows = await conn.fetch(_RANK_SQL)
    return {r["code"]: int(r["importance_rank"]) for r in rows}


def fetch_exchange_map(conn) -> Dict[str, str]:
    """Sync (psycopg) {code: exchange} from the stats table (non-NULL)."""
    with conn.cursor() as cur:
        cur.execute(_EXCHANGE_SQL)
        return {code: exch for code, exch in cur.fetchall()}


async def fetch_exchange_map_async(conn) -> Dict[str, str]:
    """Async (asyncpg) {code: exchange} from the stats table (non-NULL)."""
    rows = await conn.fetch(_EXCHANGE_SQL)
    return {r["code"]: r["exchange"] for r in rows}


def owned_by_szse_downloader(exchange_map: Dict[str, str], code: str) -> bool:
    """True when ``code``'s download service is the SZSE downloader.

    The routing rule behind the exchange column: codes whose (curated)
    exchange is :data:`SZSE_DOWNLOADER_EXCHANGE` are SZSE-exchange-
    PUBLISHED indices csindex.com.cn does not serve — every csindex
    service must skip them and the SZSE downloader/streamer owns them.
    An unknown code (absent from the map) is NOT owned — callers keep
    their legacy hardcoded fallbacks for those.
    """
    return exchange_map.get(code) == SZSE_DOWNLOADER_EXCHANGE


def szse_downloader_codes(exchange_map: Dict[str, str]) -> List[str]:
    """Codes owned by the SZSE downloader, ascending (the always-stream /
    keep-lists of the SZSE services derive from this)."""
    return sorted(c for c, e in exchange_map.items()
                  if e == SZSE_DOWNLOADER_EXCHANGE)


def exchange_map_or_empty() -> Dict[str, str]:
    """Rank-file exchange map FIRST, then DB, then ``{}``.

    Same cache-first contract as :func:`order_codes_or_sorted` — the
    download tier reads the file so a DB outage can neither fail routing
    nor degrade it. An empty map means "no routing data" (fresh install
    before the first monthly build); callers fall back to their legacy
    hardcoded code sets and must never fail the run.
    """
    exchanges = load_exchange_map_cache()
    if exchanges:
        return exchanges
    try:
        from _common.db_commons import get_read_db_connection

        conn = get_read_db_connection()
        try:
            return fetch_exchange_map(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — routing must never fail a run
        logger.warning("exchange routing map unavailable: %s", exc)
        return {}


def order_codes_or_sorted(codes: Iterable[str]) -> List[str]:
    """Sync convenience: rank-file cache FIRST, then DB, then sorted().

    The cache-first order decouples the download tier from the DB: a DB
    outage during downloads can neither fail the sweep nor degrade the
    important-codes-first ordering. The DB read only serves a fresh
    install before the first monthly build wrote the cache. Falls back
    to plain ``sorted()`` everywhere — callers get a deterministic list.
    """
    codes = list(codes)
    ranks = load_importance_ranks_cache()
    if ranks:
        return order_codes_by_importance(codes, ranks)
    try:
        from _common.db_commons import get_read_db_connection

        conn = get_read_db_connection()
        try:
            ranks = fetch_importance_ranks(conn)
        finally:
            conn.close()
        if not ranks:
            return sorted(codes)
        return order_codes_by_importance(codes, ranks)
    except Exception as exc:  # noqa: BLE001 — ordering must never fail a run
        logger.warning("importance ordering unavailable, falling back to "
                       "sorted(): %s", exc)
        return sorted(codes)


async def order_codes_or_sorted_async(
    conn, codes: Iterable[str]
) -> List[str]:
    """Async convenience over an existing asyncpg connection.

    Same fallback contract as :func:`order_codes_or_sorted`.
    """
    codes = list(codes)
    try:
        ranks = await fetch_importance_ranks_async(conn)
        if not ranks:
            return sorted(codes)
        return order_codes_by_importance(codes, ranks)
    except Exception as exc:  # noqa: BLE001 — ordering must never fail a run
        logger.warning("importance ordering unavailable, falling back to "
                       "sorted(): %s", exc)
        return sorted(codes)


__all__ = [
    "DEFAULT_RANK",
    "RANK_TABLE",
    "SZSE_DOWNLOADER_EXCHANGE",
    "exchange_map_or_empty",
    "fetch_exchange_map",
    "fetch_exchange_map_async",
    "fetch_importance_ranks",
    "fetch_importance_ranks_async",
    "fetch_importance_ranks_or_empty",
    "load_exchange_map_cache",
    "order_codes_by_importance",
    "order_codes_or_sorted",
    "order_codes_or_sorted_async",
    "order_pairs_by_importance",
    "owned_by_szse_downloader",
    "szse_downloader_codes",
]
