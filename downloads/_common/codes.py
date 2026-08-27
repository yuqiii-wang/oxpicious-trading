"""Security-code canonicalization & classification helpers.

Canonical code schema is "NNNNNN.XX" (+ .SS/.SZ/.BJ exchange suffix) with
derived ``exchange``/``board``/``sec_type`` columns. Also hosts the
exchange-suffix inference helpers and the sec_classification.json loader.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def normalize_code_column(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Normalize the 证券代码 column: zero-pad to 6 digits and append *suffix*.

    Handles three input shapes produced by the SZSE/SSE Excel exports:
      * float   like 1.0          -> "000001.SZ"
      * str     like "000001"     -> "000001.SZ"
      * str     like "000001.SZ"  -> unchanged (idempotent)

    Only the exact column named 证券代码 is processed. Other code-like
    columns (合约编码, 标的证券简称（代码）, etc.) are left untouched.
    """
    if not suffix:
        return df
    suffix = suffix if suffix.startswith(".") else "." + suffix
    if "证券代码" not in np.asarray(df.columns).tolist():
        return df
    result = df.copy()
    series = result["证券代码"]
    # dtype==float is GPU-clean; the old `series.dtype == object` guard is
    # dropped (it warned via StringDtype.__eq__) — contains() works on any
    # dtype and float columns are covered by the first clause.
    has_float_artifact = (
        series.astype(str).str.contains(r"^\d+\.0$", regex=True, na=False).any()
    )
    if series.dtype == float or has_float_artifact:
        # Vectorized equivalent of to_numeric + .apply(int format):
        # numeric-looking values ("1", "1.0", 1.0) -> "000001" + suffix;
        # anything else (incl. NaN, which str()'s to "nan") -> "" — same
        # as the old to_numeric(errors="coerce") NaN -> "" semantics.
        s = series.astype(str).str.strip()
        is_num = s.str.match(r"^\d+(\.0+)?$", na=False)
        base = s.str.split(".").str[0]
        cleaned = (base.str.zfill(6) + suffix).where(is_num, "")
    else:
        cleaned = series.astype(str).str.strip()
        has_dot = cleaned.str.contains(r"\.", regex=True, na=False)
        padded = cleaned.str.zfill(6) + suffix
        # full-size `other` (the old subset-other where() misaligned under
        # cudf and fell back); ~has_dot rows take padded, dotted rows keep
        # the original suffixed code
        cleaned = padded.where(~has_dot, cleaned)
    result["证券代码"] = cleaned
    return result


# ---------------------------------------------------------------------------
# Canonical code schema: 证券代码 -> "NNNNNN.XX" + exchange + board + sec_type
# ---------------------------------------------------------------------------


def classify_sec_type(base6: pd.Series, exchange: pd.Series) -> pd.Series:
    """Classify rows into security types (vectorized, no regex).

    Values: index (000xxx.SS / 399xxx.SZ / 899xxx.BJ), etf (funds:
    50/51/52/56/58xxxx.SS, 15/16/18xxxx.SZ), stock (everything else).
    """
    p3 = base6.str[:3]
    p2 = base6.str[:2]
    is_index = (
        ((exchange == "SS") & (p3 == "000"))
        | ((exchange == "SZ") & (p3 == "399"))
        | ((exchange == "BJ") & (p3 == "899"))
    )
    is_etf = (
        ((exchange == "SS") & p2.isin(["50", "51", "52", "56", "58"]))
        | ((exchange == "SZ") & p2.isin(["15", "16", "18"]))
    )
    sec_type = pd.Series("stock", index=base6.index, dtype=object)
    sec_type = sec_type.mask(is_etf, "etf")
    sec_type = sec_type.mask(is_index, "index")
    return sec_type


def classify_board(base6: pd.Series, exchange: pd.Series) -> pd.Series:
    """Classify stock rows into boards (vectorized, no regex).

    Values: STAR (科创板 688/689.SS), GEM (创业板 30xxxx.SZ), BSE (Beijing),
    MAIN (everything else). Non-stock rows (etf/index) get "" — the
    ``sec_type`` column carries their class instead.
    """
    sec_type = classify_sec_type(base6, exchange)
    board = pd.Series("MAIN", index=base6.index, dtype=object)
    p3 = base6.str[:3]
    p2 = base6.str[:2]
    board = board.mask((exchange == "SS") & p3.isin(["688", "689"]), "STAR")
    board = board.mask((exchange == "SZ") & (p2 == "30"), "GEM")
    board = board.mask(exchange == "BJ", "BSE")
    return board.mask(sec_type != "stock", "")


# Name columns that may carry fund names in exchange exports (checked in order)
FUND_NAME_COL_CANDIDATES: tuple = ("证券简称", "基金简称", "简称", "name")

# Structured-fund share-class suffixes: trailing 'A级'/'B级' or a single
# trailing 'A'/'B' preceded by a CJK char or digit. Stripping these exposes
# the underlying index keyword for name-based classification:
#   '中证100A' → '中证100',  '深成指A' → '深成指',  '券商A级' → '券商'.
_SHARE_CLASS_LEVEL_RE = r"[AB]级$"                       # two-char suffix
_SHARE_CLASS_PLAIN_RE = r"(?<=[\u4e00-\u9fff\d])[AB]$"   # A/B after CJK/digit


def clean_fund_share_class_names(names: pd.Series) -> pd.Series:
    """Strip structured-fund share-class suffixes from fund names (vectorized).

    Only fund rows should be passed — stock names legitimately end with
    'A'/'B' (e.g. 万科A, 万科B) and must NOT be stripped.

    Examples: '中证100A级'→'中证100',  '中证100A'→'中证100',
              '深成指A'→'深成指',  '消费收益'→'消费收益' (unchanged).
    """
    s = names.astype(str).str.strip()
    # 1. strip trailing 'A级'/'B级' first (two-char suffix)
    s = s.str.replace(_SHARE_CLASS_LEVEL_RE, "", regex=True)
    # 2. then a single trailing 'A'/'B' when preceded by CJK or digit.
    #    English-ended names are kept (lookbehind fails on ASCII letters).
    s = s.str.replace(_SHARE_CLASS_PLAIN_RE, "", regex=True)
    return s


def canonicalize_code_column(
    df: pd.DataFrame,
    exchange: str,
    *,
    code_col: str = "证券代码",
    sec_type: str = "auto",
) -> Optional[pd.DataFrame]:
    """Canonicalize the code column and add ``exchange``/``board``/``sec_type``.

    Produces the canonical schema loaders rely on (no per-row string ops
    downstream):

    * ``证券代码``  -> "NNNNNN.XX" (6 digits + .SS/.SZ/.BJ); invalid /
      placeholder / empty rows become ""
    * ``exchange``  -> "SS"/"SZ"/"BJ" for valid rows, "" otherwise
    * ``board``     -> STAR/GEM/MAIN/BSE for stock rows, "" otherwise
    * ``sec_type``  -> "stock"/"etf"/"index" for valid rows, "" otherwise

    A row's exchange is its existing suffix when present (e.g. SSE margin
    exports "510050.SS"), otherwise the file-level *exchange* context.
    *sec_type* is per-row inferred from the code prefix by default
    ("auto"); pass an explicit "stock"/"etf"/"index" to broadcast a
    single-type file context (e.g. SSE fund-tab exports). Returns the
    transformed copy, or None when *code_col* is absent.
    """
    if code_col not in np.asarray(df.columns).tolist():
        return None
    # defensive normalization: accept "SZ"/"sz"/".SZ"/" .SZ " — the file-level
    # exchange context must be the bare 2-letter code, never a dotted suffix
    exchange = str(exchange).strip().lstrip(".").upper()
    result = df.copy()
    s = result[code_col].astype(str).str.strip()

    # "000001.SZ" -> base "000001" + suffix "SZ"; "1.0" (Excel float
    # artifact) -> base "1" + garbage suffix "0" (rejected by isin below);
    # no dot -> base as-is + "" suffix
    parts = s.str.split(".")
    base = parts.str[0]
    # fillna BEFORE .str.upper(): when no row carries a suffix (e.g. SSE
    # index snapshots with bare "000001" codes), parts.str[1] is all-NaN
    # float64 and .str.upper() on it raises AttributeError
    raw_suffix = parts.str[1].fillna("").str.upper()
    is_valid_base = base.str.match(r"^\d{1,6}$", na=False)
    base6 = base.str.zfill(6)

    row_exchange = raw_suffix.where(raw_suffix.isin(["SS", "SZ", "BJ"]), "")
    row_exchange = row_exchange.where(row_exchange != "", exchange)
    valid = is_valid_base & (row_exchange != "")

    result[code_col] = (base6 + "." + row_exchange).where(valid, "")
    result["exchange"] = row_exchange.where(valid, "")
    if sec_type == "auto":
        row_sec_type = classify_sec_type(base6, row_exchange)
    else:
        row_sec_type = pd.Series(sec_type, index=base6.index, dtype=object)
    result["sec_type"] = row_sec_type.where(valid, "")
    result["board"] = classify_board(base6, row_exchange).where(valid, "")

    # Fund-name share-class cleanup: applied at CSV-conversion time so
    # builds never need CJK string surgery (classification reads clean
    # names). Only rows classified as funds are touched — stock names
    # like 万科A keep their trailing A/B.
    name_col = next(
        (c for c in FUND_NAME_COL_CANDIDATES
         if c in np.asarray(df.columns).tolist()),
        None,
    )
    if name_col is not None:
        is_fund_row = row_sec_type.eq("etf") & valid
        if bool(is_fund_row.any()):
            raw_names = result[name_col].astype(str)
            cleaned = clean_fund_share_class_names(raw_names)
            result[name_col] = raw_names.where(~is_fund_row, cleaned)
    return result


def _normalize_raw_code(val: Any) -> str:
    """Normalize a raw code cell to a 6-digit zero-padded string.

    Handles float exports (1.0 -> "000001"), bare numeric strings
    ("399001" -> "399001"), and already-suffixed strings ("399001.SZ" ->
    "399001"). Non-numeric strings are returned unchanged.
    """
    s = str(val).strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def filter_by_code(df: pd.DataFrame, code_filter: List[str]) -> pd.DataFrame:
    """Keep only rows whose code column value matches one of *code_filter*.

    Looks for the first of these columns (in order): 证券代码, 指数代码.
    Raw cell values are normalized via :func:`_normalize_raw_code` before
    comparison, so callers can pass bare 6-digit codes like
    ``["399001", "399006"]`` regardless of how Excel exported the column
    (int, float-with-trailing-.0, or already-suffixed). Returns *df*
    unchanged if no recognized code column exists or *code_filter* is empty.
    """
    if not code_filter:
        return df
    code_col = None
    cols = np.asarray(df.columns).tolist()
    for cand in ("证券代码", "指数代码"):
        if cand in cols:
            code_col = cand
            break
    if code_col is None:
        return df
    # Vectorized equivalent of .map(_normalize_raw_code): strip, drop any
    # suffix after ".", zero-pad pure digits to 6, keep others unchanged.
    s = df[code_col].astype(str).str.strip()
    base = s.str.split(".").str[0]
    is_digit = base.str.match(r"^\d+$", na=False)
    normalized = base.str.zfill(6).where(is_digit, base)
    wanted = {_normalize_raw_code(c) for c in code_filter}
    mask = normalized.isin(list(wanted))
    return df[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Stock code normalization helpers
# ---------------------------------------------------------------------------
#
# CRITICAL: Shanghai and Shenzhen stock code prefixes overlap in the 000xxx/001xxx range!
#
# Shanghai Stock Exchange (SSE):
#   - 600xxx, 601xxx, 603xxx, 605xxx: Shanghai-listed stocks
#   - 688xxx: STAR Market (科创板)
#   - 000xxx, 001xxx: Shanghai indices (e.g., 000001=SSE Composite, 000300=CSI 300)
#
# Shenzhen Stock Exchange (SZSE):
#   - 000xxx, 001xxx: Shenzhen main board stocks (e.g., 000001=Ping An Bank)
#   - 002xxx, 003xxx: SME board (中小板)
#   - 300xxx, 301xxx: ChiNext board (创业板)
#
# AMBIGUITY: Code 000001 could refer to either:
#   - SSE Composite Index (Shanghai)
#   - Ping An Bank (Shenzhen)
#
# Solution: When processing data, ALWAYS pass the 'market' parameter to add_exchange_suffix().
# When the market is known, we use it to disambiguate. When market is not known:
#   - Unambiguous prefixes (600-605, 688, 002-003, 300-301) are auto-classified
#   - Known Shanghai index codes are mapped to .SS
#   - Other 000xxx/001xxx codes trigger a warning and are returned without suffix
#
# ETF Codes (NO overlap between exchanges):
#   Shanghai (SSE):   510xxx, 511xxx, 512xxx, 513xxx, 515xxx, 516xxx, 518xxx, 56xxx
#   Shenzhen (SZSE):  150xxx, 159xxx, 16xxx
#   Unlike stock codes, ETF prefixes are completely non-overlapping between exchanges,
#   so no suffix disambiguation is needed for ETFs.
# ---------------------------------------------------------------------------

SHANGHAI_EXCLUSIVE_PREFIXES = ("600", "601", "603", "605", "688")
SHENZHEN_EXCLUSIVE_PREFIXES = ("002", "003", "300", "301")

AMBIGUOUS_PREFIXES = ("000", "001")

SHANGHAI_BROADMARKET_INDEX_CODES = {
    "000001",
    "000002",
    "000003",
    "000008",
    "000009",
    "000016",
    "000300",
    "000905",
}


def get_exchange_from_code(stock_code: str) -> Optional[str]:
    code = str(stock_code).strip()
    if len(code) != 6:
        return None
    prefix = code[:3]
    if prefix in SHANGHAI_EXCLUSIVE_PREFIXES:
        return "SS"
    if prefix in SHENZHEN_EXCLUSIVE_PREFIXES:
        return "SZ"
    if prefix in AMBIGUOUS_PREFIXES:
        if code in SHANGHAI_BROADMARKET_INDEX_CODES:
            return "SS"
    return None


def add_exchange_suffix(stock_code: str, market: Optional[str] = None) -> str:
    code = str(stock_code).strip()
    if "." in code:
        return code
    if len(code) != 6:
        return code
    if market:
        if "上海" in market:
            return code + ".SS"
        if "深圳" in market:
            return code + ".SZ"
        if "北京" in market:
            return code + ".BJ"
        if "香港" in market:
            return code + ".HK"
    exchange = get_exchange_from_code(code)
    if exchange:
        return code + "." + exchange
    import warnings
    prefix = code[:3]
    if prefix in AMBIGUOUS_PREFIXES:
        reason = (
            f"Codes {prefix}xxx are ambiguous (used by both Shanghai indices "
            f"and Shenzhen stocks)"
        )
    else:
        reason = f"Unrecognized prefix '{prefix}' (not a stock/index prefix)"
    warnings.warn(
        f"Cannot determine exchange for code '{code}'. {reason}. "
        f"Pass 'market' parameter explicitly. Returning code without suffix."
    )
    return code


def strip_exchange_suffix(stock_code: str) -> str:
    code = str(stock_code).strip()
    if "." in code:
        parts = code.split(".")
        if len(parts) == 2 and parts[1] in ("SS", "SZ", "BJ", "HK"):
            return parts[0]
    return code


# ---------------------------------------------------------------------------
# Classification JSON loader — replacement for _classification.ICONIC_INDEXES
# ---------------------------------------------------------------------------

_CLASSIFICATION_JSON_PATH = (
    Path(__file__).resolve().parents[2] / "_common" / "sec_statics" / "sec_classification.json"
)


def load_classification_indices() -> Dict[str, Dict[str, Any]]:
    """Load index classifications from ``sec_classification.json``.

    Returns a dict keyed by index code, where each value is the full index
    entry from the JSON::

        {
            "name": str,
            "exchange": Optional[str],   # "SS" | "SZ" | "BJ" | "HK" | None
            "sector_id": str,
            "industry_id": str,
            "tags": List[Dict[str, str]],
            "n_days": int,
            "first_date": Optional[str],
            "last_date": Optional[str],
        }
    """
    import json as _json
    if not _CLASSIFICATION_JSON_PATH.is_file():
        return {}
    with _CLASSIFICATION_JSON_PATH.open("r", encoding="utf-8") as f:
        state = _json.load(f)
    return state.get("indices", {})


def load_classification_index_names() -> Dict[str, str]:
    """Return a flat ``{code: name}`` dict from ``sec_classification.json``.

    Convenience wrapper around :func:`load_classification_indices` for callers
    that only need the code → name mapping (drop-in replacement for
    ``ICONIC_INDEXES``).
    """
    return {
        code: info.get("name", code)
        for code, info in load_classification_indices().items()
    }
