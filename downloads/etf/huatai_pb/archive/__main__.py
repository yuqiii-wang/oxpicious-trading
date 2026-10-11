"""Download huatai-pb.com fund NAV (基金净值) FULL history (成立以来) archive.

One request per fund fetches the complete NAV history since inception
(510880: 4813 rows, 2006-11-17 → today — see ``_endpoints`` study notes for
the getFundNetTableJson endpoint). Default universe: the curated checkin
watchlist (CHECKIN_FUND_CODES in ``_endpoints``); the full feeder set was
fetched once via ``feeder_codes_from_latest_trend`` (builds.etf.feeder_nav
reads those CSVs + the all-funds daily trend snapshots). Output per fund:

  ``temps/huatai_pb_nav/raw/huatai_pb_nav_archive_<code>_<YYYYMMDD>.json``
      raw response archive (re-written on every successful download)
  ``temps/huatai_pb_nav/huatai_pb_nav_archive_<code>.csv``
      finished full-history CSV — date asc, canonical code

CSV schema (``date, code, nav, cum_nav, day_pct``):
  * date      — ISO trade date
  * code      — BARE 6-digit code, listed ETF and OTC fund alike (no
               exchange suffix anywhere — the 2026-09-30 convention)
  * nav       — 单位净值
  * cum_nav   — 累计净值
  * day_pct   — 日涨跌 in PERCENT (site's dayinc field, e.g. -0.505)

Skip logic: a fund whose archive CSV is fresh within 20 days is skipped
(run-once registration in downloads_once.sh — re-runs are cheap no-ops);
``--force`` re-downloads everything. Consumed by builds.etf.feeder_nav
(feeder NAV → stats.etf_identity/etf_basic_stats with is_ohl_estimated);
the 510880 reference archive has no builds/etf consumer (the market-side
OHLCV trend CSVs builds.etf reads come from the exchange downloaders).

Usage:
  python -m downloads.etf.huatai_pb.archive
  python -m downloads.etf.huatai_pb.archive --fund-codes 510880,012761
  python -m downloads.etf.huatai_pb.archive --force
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Dict, List

import pandas as pd

from downloads._common import (
    RunStats,
    build_default_session,
    is_fresh_within,
    resolve_out_dir,
    setup_logger,
)
from downloads.etf.huatai_pb._endpoints import (
    CHECKIN_FUND_CODES,
    build_proxy,
    fetch_nav_rows,
    parse_comma_number,
)

logger = setup_logger("huatai_pb_nav_archive")

SERVICE = "downloads.etf.huatai_pb.archive"

CSV_ENCODING = "utf-8-sig"
ARCHIVE_FRESH_HOURS = 20 * 24  # monthly cadence; guard bounds re-hits
# Far-past window start — the endpoint clamps to each fund's inception.
HISTORY_SINCE = "2000-01-01"

NAV_COLS = ["date", "code", "nav", "cum_nav", "day_pct"]


def nav_rows_to_frame(rows: List[Dict[str, object]], fund_code: str) -> pd.DataFrame:
    """Raw dataList dicts → canonical NAV frame (date asc).

    Codes are stored BARE (listed ETFs and OTC funds alike — no exchange
    suffix since 2026-09-30).
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=NAV_COLS)
    code = fund_code.strip()
    out = pd.DataFrame({
        "date": df["date"].astype(str),
        "code": code,
        "nav": df["netvalue"].map(parse_comma_number),
        "cum_nav": df["totalnetvalue"].map(parse_comma_number),
        "day_pct": df["dayinc"].map(parse_comma_number),
    })
    out = out.dropna(subset=["date", "nav"])
    out = out.drop_duplicates(subset=["date"], keep="first")
    return out.sort_values("date").reset_index(drop=True)


def feeder_codes_from_latest_trend(out_dir) -> List[str]:
    """ETF联接 feeder fund codes from the newest daily trend snapshot CSV.

    The products-index snapshot covers every fund share class, so the
    联接 name match yields the full feeder universe (A/C/... classes).
    NOT part of the default universe anymore (checkin codes only) — kept
    for one-off re-fetches of the full feeder history:
    ``python -m downloads.etf.huatai_pb.archive --fund-codes <list>``.
    """
    files = sorted(out_dir.glob("huatai_pb_nav_trend_*.csv"))
    if not files:
        return []
    snap = pd.read_csv(files[-1], dtype={"code": str})
    hit = snap[snap["name"].astype(str).str.contains("联接", na=False)]
    # split(".") guards legacy snapshots whose listed rows were suffixed
    return sorted({str(c).split(".")[0] for c in hit["code"].tolist()})


def download_nav_archive(
    *,
    out_root: str | None = None,
    fund_codes: List[str] | None = None,
    sleep_sec: float | None = None,
    force: bool = False,
) -> Dict[str, object]:
    out_dir = resolve_out_dir("", "huatai_pb_nav", out_root)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if fund_codes:
        codes = [c.strip() for c in fund_codes if c.strip()]
    else:
        # Default universe: the curated checkin watchlist ONLY (the daily
        # trend stays all-funds; builds.etf.feeder_nav's feeder history
        # came from a one-off full-feeder fetch — see
        # feeder_codes_from_latest_trend for re-running it).
        codes = list(CHECKIN_FUND_CODES)

    session = build_default_session()
    proxy = build_proxy(SERVICE, sleep_sec)
    stats = RunStats()
    today = date.today().strftime("%Y-%m-%d")

    for code in codes:
        csv_path = out_dir / f"huatai_pb_nav_archive_{code}.csv"
        if not force and is_fresh_within(csv_path, hours=ARCHIVE_FRESH_HOURS):
            logger.info("[archive %s] cached (fresh within %dh)", code,
                        ARCHIVE_FRESH_HOURS // 24)
            stats.skipped_cached += 1
            continue

        rows = fetch_nav_rows(session, proxy, code, HISTORY_SINCE, today,
                              logger=logger)
        if not rows:
            logger.warning("[archive %s] no NAV rows returned, skipping", code)
            stats.failed += 1
            continue

        frame = nav_rows_to_frame(rows, code)
        if frame.empty:
            logger.warning("[archive %s] %d raw rows parsed to 0, skipping",
                           code, len(rows))
            stats.failed += 1
            continue

        raw_path = raw_dir / f"huatai_pb_nav_archive_{code}_{date.today():%Y%m%d}.json"
        raw_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        frame.to_csv(csv_path, index=False, encoding=CSV_ENCODING)
        stats.downloaded += 1
        logger.info("[archive %s] saved %s (%d rows, %s → %s)",
                    code, csv_path.name, len(frame),
                    frame["date"].iloc[0], frame["date"].iloc[-1])

    summary = stats.to_dict(out_dir=str(out_dir), funds=len(codes))
    logger.info("Done NAV archive: %s", summary)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download huatai-pb.com full-history (成立以来) fund NAV archive."
    )
    ap.add_argument("--fund-codes", type=str, default=None,
                    help="Comma-separated fund codes (default: the checkin "
                         "watchlist — see _endpoints.CHECKIN_FUND_CODES)")
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--sleep-sec", type=float, default=None,
                    help="Sleep seconds between requests "
                         f"(default: external_request_config JSON for {SERVICE})")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even when the archive CSV is fresh.")
    args = ap.parse_args()
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)

    fund_codes_arg = None
    if args.fund_codes:
        fund_codes_arg = [c.strip() for c in args.fund_codes.split(",") if c.strip()]

    download_nav_archive(
        out_root=args.out_root,
        fund_codes=fund_codes_arg,
        sleep_sec=args.sleep_sec,
        force=args.force,
    )
