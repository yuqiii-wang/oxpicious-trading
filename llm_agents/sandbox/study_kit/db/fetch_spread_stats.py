"""study_kit.db.fetch_spread_stats — the TABLE-FIRST MA-spread read.

Retrieves the materialized trailing stats of the 9 MA-spread gaps
(gap + trailing-252d pct_rank + zscore + stretched verdict) straight
from the split pair analysis.mov_ave_spreads_detail (gap values) ⋈
analysis.mov_ave_spreads_detail_trailing (pct_rank / zscore / verdict)
— the numbers ``study_kit.studies.spread_report`` would otherwise
recompute from bars. Use THIS first for the standard pairs/window;
compute only for custom windows or parameters the tables do not carry.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

from study_kit.db._shared import SEC_TYPE_CODES, require_sec_type
from study_kit.db.query import query

# The 12 gap pairs materialized on the detail table pair (the 9
# originals + the 3 senior inter-MA legs, 2026-11-09 — the
# close>ma5>ma20>ma60>ma120>ma252 alignment chain the deep-agent
# studies used to recompute).
ALL_PAIRS: tuple[str, ...] = (
    "price_vs_ma5", "price_vs_ma20", "price_vs_ma60",
    "price_vs_ma120", "price_vs_ma252",
    "ma5_vs_ma20", "ma5_vs_ma60", "ma5_vs_ma120", "ma5_vs_ma252",
    "ma20_vs_ma60", "ma60_vs_ma120", "ma120_vs_ma252",
)

# The spread_verdict label decode (static.label_dim, snake_case labels
# — the same vocabulary study_kit.studies.spread_report returns).
VERDICT_LABELS: dict[int, str] = {
    0: "stretched_low", 1: "neutral", 2: "stretched_high",
}


def fetch_spread_stats(
    sec_type: str,
    code: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    pairs: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Materialized MA-spread trailing stats, newest last — NO compute.

    One row per date with, per selected gap pair: the gap value (from
    analysis.mov_ave_spreads_detail), its trailing-252d ``_pct_rank``
    (0..1) and ``_zscore``, and the decoded ``_verdict`` label
    (stretched_low / neutral / stretched_high; NULL while the code's
    trailing window was not yet realized — from the same-grain companion
    analysis.mov_ave_spreads_detail_trailing, joined on (sec_type,
    code, date)). Backing tables' universe: etf / index / stock /
    industry rows the parent pipeline publishes (indices since 2020).

    ``pairs`` selects a subset of the 9 names in ALL_PAIRS (None = all).
    ``start``/``end`` are inclusive 'YYYY-MM-DD' strings (default: the
    code's full stored history). For CUSTOM windows / thresholds /
    sec_types the tables do not cover, fall back to
    ``study_kit.studies.spread_report`` over ``fetch_daily_bars``.
    """
    sec_code = require_sec_type(sec_type, SEC_TYPE_CODES)
    wanted = list(pairs) if pairs is not None else list(ALL_PAIRS)
    unknown = [p for p in wanted if p not in ALL_PAIRS]
    if unknown:
        raise ValueError(
            f"pairs {unknown}: expected subsets of {list(ALL_PAIRS)}")
    selects = ["d.date", "d.code"]
    for pair in wanted:
        selects.append(f"d.{pair}")
        selects.append(f"t.{pair}_pct_rank")
        selects.append(f"t.{pair}_zscore")
    conditions = "d.sec_type = %s AND d.code = %s"
    params: list = [sec_code, code]
    if start is not None:
        conditions += " AND d.date >= %s"
        params.append(start)
    if end is not None:
        conditions += " AND d.date <= %s"
        params.append(end)
    # One label_dim scalar subquery per verdict column (the index-join
    # decode pattern — no CASE) decodes the SMALLINT codes to labels.
    verdict_selects = ", ".join(
        f"(SELECT l.label FROM static.label_dim l "
        f"WHERE l.domain = 'spread_verdict' "
        f"  AND l.code = t.{pair}_verdict) AS {pair}_verdict"
        for pair in wanted
    )
    sql = (
        f"SELECT {', '.join(selects)}, {verdict_selects} "
        "FROM analysis.mov_ave_spreads_detail d "
        "JOIN analysis.mov_ave_spreads_detail_trailing t "
        "  ON t.sec_type = d.sec_type AND t.code = d.code "
        " AND t.date = d.date "
        f"WHERE {conditions} ORDER BY d.date ASC"
    )
    df = query(sql, params)
    if df.empty:
        raise ValueError(
            f"no spread stats for {sec_type} {code} over "
            f"[{start}, {end}] — the detail tables' universe may not "
            "cover it; fall back to studies.spread_report over "
            "fetch_daily_bars")
    return df
