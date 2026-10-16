"""Reader for the hand-curated top-rank code lists (importance_rules/).

Importance ranks 0 and 1 of every sec_type are HAND-CURATED, not computed:
``database/sec_statics/importance_rules/<sec_type>_<rank>_rank.json``
(index_0_rank.json, index_1_rank.json, etf_0_rank.json, …) each hold a
hardcoded map of high-priority codes to their EXCHANGE. The monthly builder
(``builds.classification.importance``) reads them via :func:`load_curated_ranks`
and :func:`load_curated_exchanges` and assigns the listed ranks verbatim —
a curated code overrides whatever the quantitative ladder would say, and the
computed ladder never assigns ranks 0-1 (it starts at 2).

File shape::

    {
      "comment": "<free-text, what belongs here / who curated it>",
      "codes": {"000001": "SS", "399001": "SZSE", …}
    }

``codes`` maps every curated code to its exchange — the DOWNLOAD-SERVICE
assignment key written to ``stats.sec_importance_ranks.exchange`` by the
monthly build. Values use the sec_classification exchange vocabulary
(SS / SZ / GEM / STAR / BJ / HK / OVERSEAS) plus the finer hand-curated
marker ``SZSE`` = SZSE-exchange-PUBLISHED index that csindex.com.cn does
not serve (the SZSE downloader/streamer owns the code). A legacy LIST form
``"codes": ["000001", …]`` is still accepted (exchange falls back to
sec_classification per code).

Codes use the sec_classification format: index/ETF bare 6-digit, stock WITH
exchange suffix (e.g. 600519.SS). Missing files are tolerated (empty list —
the builder then simply leaves ranks 0-1 unassigned that month).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "importance_rules"

SEC_TYPES: Tuple[str, ...] = ("index", "etf", "stock")
CURATED_RANKS: Tuple[int, ...] = (0, 1)


def curated_rank_path(sec_type: str, rank: int) -> Path:
    """Path of the curated-list JSON for one (sec_type, rank)."""
    return DATA_DIR / f"{sec_type}_{rank}_rank.json"


def _read_curated_file(path: Path) -> List[Tuple[str, str]]:
    """Parse one curated JSON into ``[(code, exchange_or_empty), …]``.

    Accepts the dict form ``{"codes": {code: exchange}}`` (exchange may be
    ``None`` — falls back to sec_classification per code) and the legacy
    list form ``{"codes": [code, …]}`` / bare ``[code, …]``. Codes are
    stripped and empties dropped. An unparseable file yields ``[]`` with a
    warning — curation gaps must not fail the monthly build.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("curated rank file unreadable (%s): %s",
                       path.name, exc)
        return []
    raw = data.get("codes", []) if isinstance(data, dict) else data
    out: List[Tuple[str, str]] = []
    if isinstance(raw, dict):
        for code, exch in raw.items():
            code = str(code).strip()
            if code:
                out.append((code, str(exch).strip().upper()
                            if isinstance(exch, str) and exch.strip() else ""))
    else:
        for code in raw:
            code = str(code).strip()
            if code:
                out.append((code, ""))
    return out


def load_curated_ranks() -> Dict[Tuple[str, int], List[str]]:
    """Load every curated top-rank list: {(sec_type, rank): [codes, …]}.

    A code listed in BOTH rank files of its type keeps the LOWER rank
    (0 wins) with a warning. A missing file logs a warning and yields an
    empty list — curation gaps must not fail the monthly build.
    """
    out: Dict[Tuple[str, int], List[str]] = {}
    for sec_type in SEC_TYPES:
        claimed: Dict[str, int] = {}
        for rank in CURATED_RANKS:
            path = curated_rank_path(sec_type, rank)
            entries = (_read_curated_file(path) if path.is_file() else [])
            if not path.is_file():
                logger.warning("curated rank file missing: %s "
                               "(ranks %d of %s stay unassigned)",
                               path.name, rank, sec_type)
            # a code listed in a LOWER rank file already wins — drop it
            # from this list so the builder's overlay can apply in any order
            codes: List[str] = []
            for code, _exch in entries:
                if code in claimed:
                    logger.warning(
                        "code %s listed in BOTH %s rank %d and rank %d — "
                        "keeping rank %d", code, sec_type, claimed[code],
                        rank, claimed[code])
                    continue
                codes.append(code)
            out[(sec_type, rank)] = codes
            for code in codes:
                claimed[code] = rank
    return out


def load_curated_exchanges() -> Dict[str, str]:
    """Load the curated per-code exchange overrides: {code: exchange}.

    Only codes whose curated entry carries a non-empty exchange are
    returned (empty/legacy entries fall back to sec_classification's
    exchange at build time). A code listed under both ranks of its type
    keeps the rank-0 entry, mirroring :func:`load_curated_ranks`.
    """
    out: Dict[str, str] = {}
    for sec_type in SEC_TYPES:
        for rank in CURATED_RANKS:
            path = curated_rank_path(sec_type, rank)
            if not path.is_file():
                continue
            for code, exch in _read_curated_file(path):
                if exch and code not in out:
                    out[code] = exch
    return out
