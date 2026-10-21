"""Reader for the hand-curated code-exclusion lists (exclusion_rules/).

The exclusion-side mirror of :mod:`importance_rules_store`: codes that a
download/build service must NOT fetch (or the rank table must not carry)
are curated in
``database/sec_statics/exclusion_rules/<sec_type>_excluded.json``
(index_excluded.json, etf_excluded.json, stock_excluded.json) — never
hardcoded in a service module. Services import their skip-set from here
so the JSON stays the single source (edit the list, not the code), the
same curation contract as the importance_rules JSONs.

File shape (same shape as the importance rank files — ``codes`` maps
every code to ONE string value; here the value is the exclusion SCOPE)::

    {
      "comment": "<free-text, what belongs here / who curated it>",
      "codes": {"399237": "all", "000823": "csindex", …}
    }

Scope vocabulary (comma-joined when a code carries several):

  ``all``          dead everywhere: never ranked (the monthly importance
                   build drops it from ``stats.sec_importance_ranks``)
                   and every service skips it. Delisted stocks/ETFs,
                   indices with no reachable data source.
  ``csindex``      csindex.com.cn serves NOTHING for the code (daily
                   export, PE, intraday) — another downloader owns its
                   daily data (SSE bulk trend, SZSE downloader, cnindex
                   archive). The csindex quote runner and streamer skip
                   it.
  ``csindex_pe``   only the PE (peg) series never returns; the daily
                   export works, so just the PE step skips the code.
  ``stream``       intraday ticks are never available from any streamer
                   (csindex intraday API "no ticks", cnindex stream NO
                   DATA); daily data flows normally.

Scope ``all`` is implicit in every scope filter (a dead code is excluded
from everything), so :func:`excluded_codes` with ``scopes=("csindex",)``
returns both csindex-scoped and all-scoped codes. A legacy LIST form
``"codes": ["399237", …]`` (and a bare list file) is accepted like the
importance store accepts it — every listed code defaults to scope
``all``.

Codes use the sec_classification format: index/ETF bare 6-digit, stock
WITH exchange suffix (e.g. 600519.SS). Missing files are tolerated
(warning + empty map — services keep running with no exclusions), and
an unparseable file yields a warning, never a failure: an exclusion
list must not break a run.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Set, Tuple

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "exclusion_rules"

SEC_TYPES: Tuple[str, ...] = ("index", "etf", "stock")

SCOPE_ALL = "all"
KNOWN_SCOPES: FrozenSet[str] = frozenset(
    {"all", "csindex", "csindex_pe", "stream"})

# multi-scope values are comma/space joined ("csindex,stream")
_SCOPE_SPLIT = re.compile(r"[,\s]+")


def excluded_path(sec_type: str) -> Path:
    """Path of the exclusion JSON for one sec_type."""
    return DATA_DIR / f"{sec_type}_excluded.json"


def _parse_scopes(code: str, raw: str) -> FrozenSet[str]:
    """Normalize one file value into the code's scope set.

    Mirrors the importance store's value handling: stripped + lowercased;
    an empty/missing value defaults to ``all`` (the conservative reading
    of a bare entry — same default as the legacy list form). Unknown
    scope tags are warned and kept — the vocabulary grows by editing
    this module, not by silently dropping curated rows.
    """
    tokens = [t for t in _SCOPE_SPLIT.split(str(raw or "").strip().lower())
              if t] or [SCOPE_ALL]
    scopes = frozenset(tokens)
    unknown = scopes - KNOWN_SCOPES
    if unknown:
        logger.warning("exclusion %s: unknown scope(s) %s kept as-is "
                       "(known: %s)", code, sorted(unknown),
                       sorted(KNOWN_SCOPES))
    return scopes


def _read_excluded_file(path: Path) -> List[Tuple[str, FrozenSet[str]]]:
    """Parse one exclusion JSON into ``[(code, scopes), …]``.

    Accepts the dict form ``{"codes": {code: scope}}`` (value may be
    ``None`` — defaults to ``all``) and the legacy list form
    ``{"codes": [code, …]}`` / bare ``[code, …]`` (every code ``all``) —
    the same two forms :func:`importance_rules_store.\
_read_curated_file` accepts. Codes are stripped and empties dropped. An
    unparseable file yields ``[]`` with a warning — exclusion gaps must
    not fail a run.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("exclusion file unreadable (%s): %s",
                       path.name, exc)
        return []
    raw = data.get("codes", {}) if isinstance(data, dict) else data
    out: List[Tuple[str, FrozenSet[str]]] = []
    if isinstance(raw, dict):
        for code, scope_val in raw.items():
            code = str(code).strip()
            if code:
                out.append((code, _parse_scopes(code, scope_val)))
    else:
        for code in raw:
            code = str(code).strip()
            if code:
                out.append((code, frozenset({SCOPE_ALL})))
    return out


def load_exclusions(sec_type: str) -> Dict[str, FrozenSet[str]]:
    """Load one sec_type's exclusions: ``{code: scopes}``.

    A missing file logs a warning and yields ``{}`` — the file for that
    type simply has no exclusions yet (same contract as
    :func:`importance_rules_store.load_curated_ranks` for missing rank
    files).
    """
    path = excluded_path(sec_type)
    if not path.is_file():
        logger.warning("exclusion file missing: %s (no %s exclusions)",
                       path.name, sec_type)
        return {}
    return dict(_read_excluded_file(path))


def load_excluded_codes(
    scopes: Tuple[str, ...] | None = None,
) -> Dict[str, Set[str]]:
    """Load every sec_type's exclusion lists: ``{sec_type: {codes}}``.

    The one-call bulk loader the monthly importance build uses (mirrors
    :func:`importance_rules_store.load_curated_ranks`'s role on the
    importance side). ``scopes=None`` → every excluded code regardless
    of scope; otherwise a code qualifies when its scope set intersects
    ``scopes`` — scope ``all`` is always included in the filter.
    """
    out: Dict[str, Set[str]] = {}
    for sec_type in SEC_TYPES:
        out[sec_type] = excluded_codes(sec_type, scopes=scopes)
    return out


def excluded_codes(sec_type: str, scopes=None) -> Set[str]:
    """Codes excluded for ``sec_type`` under the requested scope filter.

    ``scopes=None`` → every excluded code regardless of scope. Otherwise
    a code is returned when its scope set intersects ``scopes`` — scope
    ``all`` is always included in the filter (a dead code is excluded
    from everything), so ``excluded_codes("index", ("csindex",))``
    covers csindex-scoped AND all-scoped codes.
    """
    entries = load_exclusions(sec_type)
    if scopes is None:
        return set(entries)
    wanted = frozenset(scopes) | {SCOPE_ALL}
    return {c for c, s in entries.items() if s & wanted}


def exclusion_scopes(sec_type: str, code: str) -> FrozenSet[str]:
    """The exclusion scopes of one code; empty (no exclusion) when absent."""
    return load_exclusions(sec_type).get(code, frozenset())


def is_excluded(sec_type: str, code: str, scopes=None) -> bool:
    """True when ``code`` is excluded (``all`` always excluded; other
    scopes only when listed in ``scopes``)."""
    entry = exclusion_scopes(sec_type, code)
    if not entry:
        return False
    if scopes is None:
        return True
    return bool(entry & (frozenset(scopes) | {SCOPE_ALL}))


__all__ = [
    "KNOWN_SCOPES",
    "SCOPE_ALL",
    "SEC_TYPES",
    "excluded_codes",
    "excluded_path",
    "exclusion_scopes",
    "is_excluded",
    "load_excluded_codes",
    "load_exclusions",
]
