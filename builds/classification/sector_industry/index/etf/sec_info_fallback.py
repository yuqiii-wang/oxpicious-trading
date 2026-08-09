"""Re-classify ETFs that remained OTHER using the official sec_info.name.

After the main ETF classification pipeline runs (CSV inheritance + name rules
+ unmatched-fund name rules), some ETFs still have sector_id=OTHER because:

  * The CSV ``etf_name`` was an abbreviated trading name (e.g. '瑞和远见',
    '银华稳进', '保证金') that carries no index/theme keyword.
  * The v_etf_margin ``name`` was similarly opaque.

However, ``stats.sec_info`` (loaded from SZSE ETF quarterly reports) stores
the OFFICIAL fund name (基金简称) which embeds the tracking index / theme
verbatim — e.g. '国投瑞银沪深300指数分级', '易方达保证金货币',
'华夏MSCI中国A50互联互通ETF'.  This module retries classification with
that richer name.

Scope:
  sec_info only contains SZSE-listed funds (159xxx / 150xxx / 16xxxx), so
  this fallback only helps SZ ETFs — SS-listed ETFs (510xxx / 513xxx) are
  never in sec_info and are left untouched.

Only ETFs whose sector_id is still DEFAULT_SECTOR_ID after the main pipeline
are re-checked.  If the sec_info.name classification succeeds, the ETF's
sector_id / industry_id / is_industry_not_strategy are updated IN PLACE.

This step runs AFTER classify_unmatched_funds and BEFORE create_dummy_indices
so that re-classified ETFs land in their proper industry's dummy group (or
no dummy at all if they already had a real parent) instead of DUMMY_OTHER.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    classify_etf_by_name,
    classify_etf_strategy_by_name,
)


async def fetch_sec_info_names(conn, etf_codes: list[str]) -> Dict[str, str]:
    """Fetch the official sec_info.name for the given ETF codes.

    Args:
        conn: asyncpg connection (may be None — returns empty dict).
        etf_codes: ETF codes WITH exchange suffix (e.g. '159001.SZ',
                   '150009.SZ'). Only .SZ codes can match sec_info.

    Returns:
        {etf_code: sec_info.name} for codes present in sec_info.
    """
    if not etf_codes or conn is None:
        return {}
    # sec_info.code is BARE 6-digit; JOIN via code || '.SZ' = etf_code.
    # Filter to .SZ codes client-side so the query stays index-friendly.
    sz_codes = [c for c in etf_codes if c.endswith(".SZ")]
    if not sz_codes:
        return {}
    rows = await conn.fetch(
        """
        SELECT s.code || '.SZ' AS etf_code, s.name
          FROM stats.sec_info s
         WHERE s.code || '.SZ' = ANY($1::text[])
        """,
        sz_codes,
    )
    return {r["etf_code"]: r["name"] for r in rows if r["name"]}


def reclassify_other_etfs(
    etfs: Dict[str, Any],
    sec_info_names: Dict[str, str],
    verbose: bool = True,
) -> Tuple[int, int]:
    """Re-classify ETFs still at OTHER using their sec_info.name.

    Mutates ``etfs`` in place: updates sector_id / industry_id /
    is_industry_not_strategy for ETFs that gain a classification.

    Args:
        etfs: ETF dict keyed by code (WITH exchange suffix).
        sec_info_names: {etf_code: official_name} from fetch_sec_info_names.
        verbose: print a summary line when any ETF was re-checked.

    Returns:
        (n_reclassified, n_checked) where n_checked is the count of OTHER
        ETFs that had a sec_info.name available (regardless of whether the
        re-classification succeeded).
    """
    n_checked = 0
    n_reclassified = 0

    for etf_code, v in etfs.items():
        if v.get("sector_id", DEFAULT_SECTOR_ID) != DEFAULT_SECTOR_ID:
            continue  # already classified — skip
        sec_name = sec_info_names.get(etf_code)
        if not sec_name:
            continue  # not in sec_info (SS ETF, or missing report)
        n_checked += 1

        # Industry rules first, then strategy rules — same precedence as
        # classify_etfs().  classify_etf_by_name handles IB names, legal-
        # suffix stripping, and LOF markers internally.
        name_sector, _, name_industry, _ = classify_etf_by_name(sec_name, "")
        if name_sector != DEFAULT_SECTOR_ID:
            v["sector_id"] = name_sector
            v["industry_id"] = name_industry
            v["is_industry_not_strategy"] = True
            n_reclassified += 1
            continue

        strat_sector, _, strat_industry, _ = classify_etf_strategy_by_name(sec_name, "")
        if strat_sector != DEFAULT_SECTOR_ID:
            v["sector_id"] = strat_sector
            v["industry_id"] = strat_industry
            v["is_industry_not_strategy"] = False
            n_reclassified += 1

    if verbose and n_checked:
        print(f"    [SEC_INFO] {n_checked} OTHER ETFs found in sec_info, "
              f"{n_reclassified} re-classified by official name",
              flush=True)

    return n_reclassified, n_checked
