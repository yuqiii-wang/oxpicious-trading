"""Sharded reader/writer for the sec_classification data directory.

Replaces the former single-file ``sec_classification.json``
cache with a multi-layer directory so each file holds only a few items:

    sec_classification/
    ├── meta.json                    # {"version", "built_at", "csv_source"}
    ├── catalog/
    │   ├── 00_OTHER.json            # per-sector shards; the NN_ filename
    │   ├── 01_DEBT.json             # prefix preserves catalog order (the
    │   └── …                        # hand-edited order is NOT derivable
    │                                # from the JSON rule configs)
    └── indices/
        ├── MAT/RARE_METALS.json     # {code: entry, …} — one shard per
        └── …                        # (sector_id, industry_id), a few
                                     # entries each

This module is the SINGLE SOURCE OF TRUTH for the layout: the build writer
(builds.classification.sector_industry.json_io) and every reader
(downloads._common.codes, builds.text.keywords, study_select_etf) go through
it.  All loaders tolerate a missing/partial tree (empty dict), matching the
old readers' behaviour when the file was absent; :func:`load_state` returns
None when the whole directory is absent (old load_json semantics).

Writes are whole-tree: build a temp sibling dir, then swap it in — a crash
mid-write can only lose the whole cache (rebuilt on the next build), never
leave a half-updated mix, which is strictly safer than the old in-place
single-file write.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "sec_classification"
META_NAME = "meta.json"
CATALOG_DIRNAME = "catalog"
INDICES_DIRNAME = "indices"

_TMP_SUFFIX = ".tmp"
# catalog shard filename: "NN_<sector_id>.json" — NN preserves catalog order
_SHARD_PREFIX_RE = re.compile(r"^\d{2}_")


def load_meta() -> Dict[str, Any]:
    """Return the meta envelope ({"version", "built_at", "csv_source"}); {} if absent."""
    path = DATA_DIR / META_NAME
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_catalog() -> Dict[str, Any]:
    """Assemble the catalog dict {sector_id: sector} from the per-sector shards.

    Sector shards are read in filename order and the ``NN_`` prefix stripped,
    so the assembled dict preserves the catalog order used for industry
    tie-breaking (builds.text.keywords).  {} if the tree is absent.
    """
    catalog_dir = DATA_DIR / CATALOG_DIRNAME
    if not catalog_dir.is_dir():
        return {}
    catalog: Dict[str, Any] = {}
    for path in sorted(catalog_dir.glob("*.json")):
        sector_id = _SHARD_PREFIX_RE.sub("", path.stem)
        with path.open("r", encoding="utf-8") as f:
            catalog[sector_id] = json.load(f)
    return catalog


def load_indices() -> Dict[str, Dict[str, Any]]:
    """Assemble the indices dict {code: entry} from the sector/industry shards.

    Shards are read in sorted (sector dir, filename) order and merged; codes
    inside a shard are stored sorted by the writer.  {} if the tree is absent.
    """
    indices_dir = DATA_DIR / INDICES_DIRNAME
    if not indices_dir.is_dir():
        return {}
    indices: Dict[str, Dict[str, Any]] = {}
    for sector_dir in sorted(p for p in indices_dir.iterdir() if p.is_dir()):
        for path in sorted(sector_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as f:
                indices.update(json.load(f))
    return indices


def load_state() -> Optional[Dict[str, Any]]:
    """Assemble the full state dict, or None when the data dir is absent.

    Same shape (and same None-when-missing contract) as the former
    sec_classification.json had for builds.json_io.load_json().
    """
    if not DATA_DIR.is_dir():
        return None
    meta = load_meta()
    return {
        "version": meta.get("version", 1),
        "built_at": meta.get("built_at", ""),
        "csv_source": meta.get("csv_source", ""),
        "catalog": load_catalog(),
        "indices": load_indices(),
    }


def write_state(state: Dict[str, Any]) -> None:
    """Rewrite the whole shard tree from a full state dict.

    ``state`` holds {"version", "built_at", "csv_source", "catalog",
    "indices"} — the same envelope save_json used to dump to the single
    file.  Catalog order (dict position) is encoded in the ``NN_`` shard
    filename prefixes; index entries are grouped into
    ``indices/<sector_id>/<industry_id>.json`` shards keyed by code, sorted
    within each shard.
    """
    catalog: Dict[str, Any] = state["catalog"]
    indices: Dict[str, Dict[str, Any]] = state["indices"]

    tmp_dir = DATA_DIR.with_name(DATA_DIR.name + _TMP_SUFFIX)
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    (tmp_dir / CATALOG_DIRNAME).mkdir(parents=True)
    (tmp_dir / INDICES_DIRNAME).mkdir(parents=True)

    with (tmp_dir / META_NAME).open("w", encoding="utf-8") as f:
        json.dump({"version": state.get("version", 1),
                   "built_at": state.get("built_at", ""),
                   "csv_source": state.get("csv_source", "")},
                  f, ensure_ascii=False, indent=2)

    for i, (sector_id, sector) in enumerate(catalog.items()):
        shard = tmp_dir / CATALOG_DIRNAME / f"{i:02d}_{sector_id}.json"
        with shard.open("w", encoding="utf-8") as f:
            json.dump(sector, f, ensure_ascii=False, indent=2)

    shards: Dict[tuple, Dict[str, Any]] = {}
    for code, entry in indices.items():
        key = (entry.get("sector_id", "OTHER"), entry.get("industry_id", "OTHER"))
        shards.setdefault(key, {})[code] = entry
    for (sector_id, industry_id), entries in shards.items():
        sector_dir = tmp_dir / INDICES_DIRNAME / sector_id
        sector_dir.mkdir(exist_ok=True)
        shard = sector_dir / f"{industry_id}.json"
        with shard.open("w", encoding="utf-8") as f:
            json.dump({code: entries[code] for code in sorted(entries)},
                      f, ensure_ascii=False, indent=2)

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    tmp_dir.replace(DATA_DIR)
    logger.info(f"    [JSON] Saved {DATA_DIR} "
                f"({len(indices)} indices in {len(shards)} shards, "
                f"{len(catalog)} catalog sectors)")
