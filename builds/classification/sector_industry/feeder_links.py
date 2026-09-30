"""ETF feeder links — load 联接基金 → target ETF map into stats.sec_etf_links.

Reads the latest ``etf_feeder_map_YYYY-MM-DD.csv`` produced by
``downloads.etf.csindex.linked_etf`` (CSIndex fund list, fundType
"联接基金" rows) and TRUNCATE+rebuilds ``stats.sec_etf_links`` — the table
is a ~700-row snapshot registry (same rebuild pattern as sec_owners).

Market design documented in database/sql/stats/19_sec_etf_links.sql: a
feeder fund invests ≥90% of NAV in ONE listed target ETF, always same
manager + same index; the link is the ETF-level mirror of the ETF→index
hierarchy (sec_classification.parent_index_code).
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from builds.classification.sector_industry.paths import CSV_DIR

import logging
logger = logging.getLogger(__name__)

CSV_ENCODING = "utf-8-sig"

# CSV column → table column (etf_feeder_map_* schema written by the download).
FEEDER_COLUMN_MAP: Dict[str, str] = {
    "联接基金代码": "feeder_code",
    "联接基金名称": "feeder_name",
    "发起式": "is_initiator",
    "目标ETF代码": "target_etf_code",
    "目标ETF名称": "target_etf_name",
    "目标ETF类型": "target_etf_type",
    "标的指数代码": "index_code",
    "标的指数": "index_name",
    "管理人": "manager",
    "资产净值（亿元）": "aum_yi",
    "成立日期": "inception_date",
}


def find_latest_feeder_csv() -> Optional[str]:
    """Latest ``etf_feeder_map_YYYY-MM-DD.csv`` in CSV_DIR (or None)."""
    if not os.path.isdir(CSV_DIR):
        return None
    candidates = sorted(
        f for f in os.listdir(CSV_DIR)
        if f.startswith("etf_feeder_map_") and f.endswith(".csv")
    )
    return os.path.join(CSV_DIR, candidates[-1]) if candidates else None


def load_feeder_rows(csv_path: str) -> List[Dict[str, Any]]:
    """Feeder map CSV → list of row dicts keyed by table column names."""
    df = pd.read_csv(csv_path, dtype=str, encoding=CSV_ENCODING, keep_default_na=False)
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        get = lambda chn: str(r.get(chn, "")).strip()  # noqa: E731
        # strip guard: feeder-map CSVs written before 2026-09-30 carry a
        # suffixed 目标ETF代码 — target codes are BARE now
        target = get("目标ETF代码").split(".")[0]
        feeder = get("联接基金代码")
        if not feeder or not target:
            continue
        try:
            aum = float(get("资产净值（亿元）")) if get("资产净值（亿元）") else None
        except ValueError:
            aum = None
        inception = get("成立日期") or None
        rows.append({
            "feeder_code": feeder,
            "feeder_name": get("联接基金名称"),
            "is_initiator": get("发起式").lower() in ("true", "1"),
            "target_etf_code": target,
            "target_etf_name": get("目标ETF名称"),
            "target_etf_type": get("目标ETF类型"),
            "index_code": get("标的指数代码"),
            "index_name": get("标的指数"),
            "manager": get("管理人"),
            "aum_yi": aum,
            "inception_date": inception,
        })
    return rows


async def rebuild_etf_links(conn, verbose: bool = False) -> int:
    """Truncate + rebuild stats.sec_etf_links from the latest feeder CSV.

    Returns the number of rows written (0 when no CSV is on disk yet).
    """
    csv_path = find_latest_feeder_csv()
    if csv_path is None:
        logger.warning(
            "    [ETF-LINKS] No etf_feeder_map_*.csv in %s — run "
            "downloads.etf.csindex.linked_etf first; stats.sec_etf_links "
            "left untouched", CSV_DIR)
        return 0

    rows = load_feeder_rows(csv_path)
    if not rows:
        logger.warning("    [ETF-LINKS] %s parsed to 0 rows — table left untouched",
                       os.path.basename(csv_path))
        return 0
    if verbose:
        logger.info("    [ETF-LINKS] %s: %d feeder → target ETF links",
                    os.path.basename(csv_path), len(rows))

    await conn.execute("TRUNCATE TABLE stats.sec_etf_links")
    await conn.executemany(
        """
        INSERT INTO stats.sec_etf_links (
            feeder_code, feeder_name, is_initiator,
            target_etf_code, target_etf_name, target_etf_type,
            index_code, index_name, manager, aum_yi, inception_date
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
        )
        """,
        [
            (r["feeder_code"], r["feeder_name"], r["is_initiator"],
             r["target_etf_code"], r["target_etf_name"], r["target_etf_type"],
             r["index_code"], r["index_name"], r["manager"], r["aum_yi"],
             date.fromisoformat(r["inception_date"]) if r["inception_date"] else None)
            for r in rows
        ],
    )
    logger.info("    [ETF-LINKS] stats.sec_etf_links rebuilt: %d rows", len(rows))
    return len(rows)
