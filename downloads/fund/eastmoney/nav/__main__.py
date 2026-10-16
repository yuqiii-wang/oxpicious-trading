"""Download OTC fund NAV (单位净值/累计净值) history from eastmoney's public
pingzhongdata for the ETF feeder funds (ETF联接基金).

The feeder universe lives in ``stats.sec_etf_links`` / the csindex
``etf_feeder_map_*.csv`` (downloads.etf.csindex.linked_etf). Feeder codes are
OTC funds — no exchange OHLCV exists — so their trend series is the NAV
published by the fund companies and aggregated by eastmoney's fund platform:

    GET https://fund.eastmoney.com/pingzhongdata/<code>.js
        (Referer: https://fund.eastmoney.com/<code>.html)

    var fS_name = "<fund name>";
    var Data_netWorthTrend = [{x: <epoch ms>, y: <unit NAV>,
                               equityReturn: <day %>, ...}, ...];
    var Data_ACWorthTrend  = [[<epoch ms>, <cum NAV>], ...];

One request per fund returns the full inception-to-date series.

Output (monthly cadence — registered in downloads_monthly.sh):

  ``temps/fund_nav/fund_nav_<code>.csv``
      per-fund full history — ``date, code, nav, cum_nav, day_pct`` (asc)
  ``temps/fund_nav/fund_nav_universe_<YYYY-MM-DD>.csv``
      registry — code, name, n_rows, first_date, latest_date

Loaded into ``stats.fund_nav`` by ``builds.fund`` and served as the
``sec_type=fund`` baseline (NAV close-line trend) for the ETF page's
Linked Feeder ETFs row-click expansion.

Usage:
  python -m downloads.fund.eastmoney.nav
  python -m downloads.fund.eastmoney.nav --fund-codes 012761,460300
  python -m downloads.fund.eastmoney.nav --max-funds 50 --sleep-sec 0.8
  python -m downloads.fund.eastmoney.nav --force
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from dataclasses import replace

from downloads._common import (
    RunStats,
    AntiBotProxy,
    antibot_config_for,
    build_default_session,
    is_fresh_within,
    resolve_out_dir,
    setup_logger,
)

logger = setup_logger("fund_nav_eastmoney")

SERVICE = "downloads.fund.eastmoney.nav"

CSV_ENCODING = "utf-8-sig"
FRESH_HOURS = 20 * 24  # monthly cadence freshness guard
PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
REFERER_URL = "https://fund.eastmoney.com/{code}.html"

NAV_COLS = ["date", "code", "nav", "cum_nav", "day_pct"]

_RE_NAME = re.compile(r'var\s+fS_name\s*=\s*"([^"]*)"')
_RE_NET = re.compile(r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);", re.S)
_RE_AC = re.compile(r"var\s+Data_ACWorthTrend\s*=\s*(\[.*?\]);", re.S)


def _ms_to_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_pingzhong(js_text: str) -> Optional[Dict[str, Any]]:
    """Parse a pingzhongdata JS payload → {name, rows} (rows date asc).

    Returns None when the payload carries no NAV trend (unknown/terminated
    fund codes return an empty array).
    """
    m_name = _RE_NAME.search(js_text)
    m_net = _RE_NET.search(js_text)
    m_ac = _RE_AC.search(js_text)
    if not m_net:
        return None
    try:
        net = json.loads(m_net.group(1))
        ac = json.loads(m_ac.group(1)) if m_ac else []
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(net, list) or not net:
        return None
    cum_by_date = {_ms_to_date(int(p[0])): p[1] for p in ac if isinstance(p, list) and len(p) == 2}

    rows: List[Dict[str, Any]] = []
    for pt in net:
        if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
            continue
        d = _ms_to_date(int(pt["x"]))
        rows.append({
            "date": d,
            "nav": float(pt["y"]),
            "cum_nav": cum_by_date.get(d),
            "day_pct": float(pt["equityReturn"]) if pt.get("equityReturn") is not None else None,
        })
    if not rows:
        return None
    return {"name": m_name.group(1) if m_name else "", "rows": rows}


def feeder_codes_from_map() -> List[str]:
    """Feeder universe: latest etf_feeder_map_*.csv from the csindex download."""
    import os
    map_dir = Path(resolve_out_dir("", "csindex_linked_etf"))
    if not map_dir.is_dir():
        return []
    candidates = sorted(
        f for f in os.listdir(map_dir)
        if f.startswith("etf_feeder_map_") and f.endswith(".csv")
    )
    if not candidates:
        return []
    df = pd.read_csv(map_dir / candidates[-1], dtype=str, encoding=CSV_ENCODING,
                     keep_default_na=False)
    col = "联接基金代码" if "联接基金代码" in df.columns else None
    if col is None:
        return []
    return [c.strip() for c in df[col] if c.strip()]


def order_feeders_by_target_rank(feeder_codes: List[str]) -> List[str]:
    """Order feeder codes by their target ETF's importance rank 0 -> 9.

    Feeds rank 0 target ETFs (flagship broad) first so a --max-funds slice
    or an interrupted monthly sweep covers the important feeder families
    first. Joins stats.sec_etf_links (feeder -> target ETF) with
    stats.sec_importance_ranks; feeders with no link/rank row sink to the
    tail (rank default 9). Falls back to the input order when the DB is
    unreachable — ordering must never fail a download run.
    """
    try:
        from _common.db_commons import get_read_db_connection

        conn = get_read_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT l.feeder_code, r.importance_rank
                      FROM stats.sec_etf_links l
                      LEFT JOIN stats.sec_importance_ranks r
                        ON r.code = l.target_etf_code
                    """
                )
                ranks = {feeder: (int(rank) if rank is not None else 9)
                         for feeder, rank in cur.fetchall()}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — ordering must never fail a run
        logger.warning("feeder importance ordering unavailable (%s); "
                       "keeping CSV order", exc)
        return feeder_codes
    # Ordering itself rides on the shared helper (rank default 9 + code
    # tiebreak); only the feeder→target-rank KEY building is local, since it
    # joins through stats.sec_etf_links.
    from _common.importance import order_codes_by_importance
    return order_codes_by_importance(feeder_codes, ranks)


def download_fund_nav(
    *,
    out_root: str | None = None,
    fund_codes: List[str] | None = None,
    sleep_sec: float | None = None,
    max_funds: Optional[int] = None,
    force: bool = False,
) -> Dict[str, object]:
    out_dir = resolve_out_dir("", "fund_nav", out_root)
    if fund_codes is None:
        # Default universe: CSV row order -> importance rank 0 -> 9 by
        # target ETF so --max-funds slices keep the important feeders.
        codes = order_feeders_by_target_rank(feeder_codes_from_map())
    else:
        codes = [c.strip() for c in fund_codes if c.strip()]
    if max_funds is not None and max_funds > 0:
        codes = codes[:max_funds]
    if not codes:
        logger.error("[main] no fund codes available (pass --fund-codes or run "
                     "downloads.etf.csindex.linked_etf first)")
        return {"out_dir": str(out_dir), "funds": 0}

    session = build_default_session()
    # Pacing from the service's external_request_config JSON row (sleep_sec,
    # sleep_jitter, timeouts, anti-bot toggles); an explicit --sleep-sec
    # overrides the row's base sleep only.
    if sleep_sec is None:
        proxy = AntiBotProxy(service=SERVICE)
    else:
        proxy = AntiBotProxy(replace(
            antibot_config_for(SERVICE), base_sleep_sec=sleep_sec,
        ))
    stats = RunStats()

    registry: List[Dict[str, Any]] = []
    for code in codes:
        csv_path = out_dir / f"fund_nav_{code}.csv"
        if not force and is_fresh_within(csv_path, hours=FRESH_HOURS):
            logger.info("[nav %s] cached (fresh within %dh)", code, FRESH_HOURS // 24)
            stats.skipped_cached += 1
            continue

        resp = proxy.get(
            session, PINGZHONG_URL.format(code=code),
            headers=None, logger=logger, log_tag=f"[nav {code}]",
        )
        if resp is None or resp.status_code != 200:
            logger.warning("[nav %s] fetch failed, skipping", code)
            stats.failed += 1
            continue

        parsed = parse_pingzhong(resp.text)
        if parsed is None:
            logger.warning("[nav %s] no NAV trend in payload, skipping", code)
            stats.empty += 1
            continue

        frame = pd.DataFrame(parsed["rows"])
        frame.insert(1, "code", code)
        frame = frame.drop_duplicates(subset=["date"], keep="first")
        frame = frame.sort_values("date").reset_index(drop=True)
        frame.to_csv(csv_path, index=False, encoding=CSV_ENCODING)
        stats.downloaded += 1
        registry.append({
            "code": code,
            "name": parsed["name"],
            "n_rows": len(frame),
            "first_date": frame["date"].iloc[0],
            "latest_date": frame["date"].iloc[-1],
        })
        logger.info("[nav %s] %s — %d rows (%s → %s)", code, parsed["name"],
                    len(frame), frame["date"].iloc[0], frame["date"].iloc[-1])

    if registry:
        reg = pd.DataFrame(registry).sort_values("code")
        reg.to_csv(out_dir / f"fund_nav_universe_{date.today().isoformat()}.csv",
                   index=False, encoding=CSV_ENCODING)

    summary = stats.to_dict(out_dir=str(out_dir), funds=len(codes))
    logger.info("Done fund NAV: %s", summary)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download OTC fund NAV history (feeder funds) from eastmoney pingzhongdata."
    )
    ap.add_argument("--fund-codes", type=str, default=None,
                    help="Comma-separated fund codes (default: feeder universe from "
                         "the latest etf_feeder_map CSV)")
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--sleep-sec", type=float, default=None,
                    help="Sleep seconds between requests "
                         f"(default: external_request_config JSON for {SERVICE})")
    ap.add_argument("--max-funds", type=int, default=None,
                    help="Limit to first N funds (dev/testing)")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even when the CSV is fresh.")
    args = ap.parse_args()
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)

    fund_codes_arg = None
    if args.fund_codes:
        fund_codes_arg = [c.strip() for c in args.fund_codes.split(",") if c.strip()]

    download_fund_nav(
        out_root=args.out_root,
        fund_codes=fund_codes_arg,
        sleep_sec=args.sleep_sec,
        max_funds=args.max_funds,
        force=args.force,
    )
