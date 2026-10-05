"""Closed-vocabulary label <-> SMALLINT code maps (the label-code contract).

stats.label_dim (database/sql/stats/21_label_dim.sql) is the DB-side
source of truth for the six converted domains; this module is the Python
mirror read/written by the pipelines. temp_scripts/check_label_dim.py
asserts the two stay identical and prints the TypeScript mirror for
data_viz/api (services/label-codes.ts).

The codes exist so label columns NEVER ride cudf.pandas frames as
strings (the cudaErrorIllegalAddress class of 26.08: string host
transfer after sort/merge/concat). Inside a frame a converted label is
an int16 code end-to-end; decode to strings ONLY at the API boundary
(TS decodes) or for display/logging — never mid-compute.

Domains (all SMALLINT NOT NULL in the target tables, 0-based):
  regime    calm=0 hot=1 panic=2 quiet=3
            (== builds.market_regimes.config.REGIMES tuple order, which
            is also lexicographic — ORDER BY code == ORDER BY label)
  side      top=0 bottom=1
  action    buy=0 sell=1
  weighting equal=0 amt=1
  rank_side HYPE=0 DRAIN=1
  pool_size small=0 mid=1 large=2 all=3
            (semantic order — differs from the old alphabetical TEXT
            order; API sites order via decode, not ORDER BY code)

sec_type / industry_id STAY TEXT in the DB (API-facing identity and
filter columns). For frame-only traffic use the INTERNAL codes below:
attach once as an int16 column, compute, then re-materialize the string
at the write boundary from the value you attached it from (a loop
constant — no per-row decode ever).
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
#  Converted domains — mirror of stats.label_dim (check_label_dim.py gates)
# ---------------------------------------------------------------------------

REGIME_CODE: dict[str, int] = {"calm": 0, "hot": 1, "panic": 2, "quiet": 3}
SIDE_CODE: dict[str, int] = {"top": 0, "bottom": 1}
ACTION_CODE: dict[str, int] = {"buy": 0, "sell": 1}
WEIGHTING_CODE: dict[str, int] = {"equal": 0, "amt": 1}
RANK_SIDE_CODE: dict[str, int] = {"HYPE": 0, "DRAIN": 1}
POOL_SIZE_CODE: dict[str, int] = {"small": 0, "mid": 1, "large": 2, "all": 3}

LABEL_MAPS: dict[str, dict[str, int]] = {
    "regime": REGIME_CODE,
    "side": SIDE_CODE,
    "action": ACTION_CODE,
    "weighting": WEIGHTING_CODE,
    "rank_side": RANK_SIDE_CODE,
    "pool_size": POOL_SIZE_CODE,
}

# code -> label per domain (int keys)
CODE_MAPS: dict[str, dict[int, str]] = {
    domain: {code: label for label, code in mapping.items()}
    for domain, mapping in LABEL_MAPS.items()
}

REGIME_LABEL: dict[int, str] = CODE_MAPS["regime"]
SIDE_LABEL: dict[int, str] = CODE_MAPS["side"]
ACTION_LABEL: dict[int, str] = CODE_MAPS["action"]
WEIGHTING_LABEL: dict[int, str] = CODE_MAPS["weighting"]
RANK_SIDE_LABEL: dict[int, str] = CODE_MAPS["rank_side"]
POOL_SIZE_LABEL: dict[int, str] = CODE_MAPS["pool_size"]

# Display order for responses that list a domain's vocabulary —
# pool_size deliberately NOT in code order here: API/UI lists keep the
# historical TEXT presentation order (alphabetical) so the contract the
# UI already renders is unchanged.
LABEL_ORDER: dict[str, tuple[str, ...]] = {
    "regime": tuple(REGIME_CODE),
    "side": tuple(SIDE_CODE),
    "action": tuple(ACTION_CODE),
    "weighting": tuple(WEIGHTING_CODE),
    "rank_side": tuple(RANK_SIDE_CODE),
    "pool_size": ("all", "large", "mid", "small"),
}


def code_labels(domain: str) -> np.ndarray:
    """Labels of ``domain`` as an object ndarray INDEXED BY CODE.

    The decode table for :func:`decode_codes` — one allocation per call
    is fine (2-4 labels per domain).
    """
    mapping = LABEL_MAPS[domain]
    return np.asarray(mapping, dtype=object)[
        np.argsort(np.fromiter(mapping.values(), dtype=np.int64))
    ]


def decode_codes(codes, domain: str) -> np.ndarray:
    """Host int-code array -> object ndarray of labels (host numpy only).

    The write/display boundary decode. ``codes`` must already be a RAW
    host ndarray (unwrap proxies via ``host_array`` first — the repo
    boundary rule); an out-of-range code raises IndexError (fail fast —
    the only valid codes are 0..len-1, no missing sentinel exists for
    these NOT NULL columns).
    """
    return code_labels(domain)[np.asarray(codes)]


# ---------------------------------------------------------------------------
#  Frame-only codes (DB columns STAY TEXT — no dim rows for these)
# ---------------------------------------------------------------------------

# Internal sec_type frame code: attach as np.int16 once, e.g.
#   df["sec_type_id"] = np.int16(SEC_TYPE_CODE[sec_type])
# then group/sort/merge on it and re-materialize the string at the write
# boundary from the same loop constant (never per-row decode).
SEC_TYPE_CODE: dict[str, int] = {"etf": 0, "index": 1, "stock": 2}
SEC_TYPE_LABEL: dict[int, str] = {c: s for s, c in SEC_TYPE_CODE.items()}
