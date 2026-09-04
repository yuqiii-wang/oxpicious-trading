"""Main orchestrator: download CSI index composition (closeweight) xls per index code.

Monthly refresh trigger: per-index check — if the latest cached CSV's month
is before yesterday's month (i.e., we crossed a month boundary since last
refresh), the cache is bypassed and that index is re-downloaded with the
CSV stamped to today's date.  This catches all month-boundary scenarios
(1st day, 2nd day after weekend, etc.) without hard-coding day==1.

Default sleep between requests is ``SLEEP_SEC`` = VERY_LONG_SLEEP_INTERVAL (300s).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from downloads._common import (
    MIN_VALID_BYTES,
    AntiBotProxy,
    AntiBotConfig,
    RunStats,
    build_default_session,
    is_valid_file,
    load_classification_indices,
    load_classification_index_names,
    resolve_out_dir,
)

from ._cache import csv_filename_for, find_cached_csv
from ._config import (
    CLOSEWEIGHT_URL_TEMPLATE,
    CSINDEX_SKIP_CODES,
    DEBT_SECTOR_ID,
    DEBT_SECTOR_INDUSTRY_IDS,
    SLEEP_SEC,
    logger,
)
from ._fetch import fetch_closeweight_xls
from ._parse import normalize_closeweight_df, parse_closeweight_xls


def download_index_composition(
    *,
    index_codes: Optional[List[str]] = None,
    out_root: Optional[str] = None,
    skip_cached: bool = True,
    sleep_sec: float = SLEEP_SEC,
    force_month_start: bool = False,
) -> dict:
    """Download CSI index composition (closeweight) for the given index codes.

    Args:
        index_codes: list of 6-digit index codes (default: ICONIC_INDEXES keys)
        out_root: alternative output root directory
        skip_cached: skip indices that already have a cached CSV
        sleep_sec: sleep between downloads
        force_month_start: force monthly-refresh behavior (bypass cache + stamp
            CSVs with today's date) regardless of cache age.
            Useful for testing the monthly refresh flow on any day.

    Returns:
        Summary dict with download stats and output directory.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "csi_index_composition", out_root)

    if not index_codes:
        index_codes = sorted(load_classification_index_names().keys())

    if not index_codes:
        logger.warning("No index codes to download (sec_classification.json has no indices)")
        return {"out_dir": str(out_dir), "downloaded": 0, "skipped_cached": 0, "failed": 0}

    # Load index metadata from sec_classification.json (replaces _classification.py).
    _index_meta = load_classification_indices()
    _index_names = {c: info.get("name", c) for c, info in _index_meta.items()}

    today = date.today()
    yday = today - timedelta(days=1)
    forced = force_month_start
    if forced:
        logger.info(
            "Force-month-start: all indices will be re-downloaded and stamped "
            "with today's date (%s).",
            today.isoformat(),
        )
    logger.info(
        "Starting CSI index composition download: %d indices, out=%s",
        len(index_codes), out_dir,
    )

    session = build_default_session()
    stats = RunStats()

    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
        sleep_jitter=0.3,
    )
    proxy = AntiBotProxy(proxy_config)

    results: List[Dict[str, Any]] = []

    try:
        for code in index_codes:
            code = str(code).strip()
            if not code:
                continue

            name = _index_names.get(code, code)

            if code in CSINDEX_SKIP_CODES:
                logger.info("== Index %s (%s) — skipped (in CSINDEX_SKIP_CODES, handled by SZSE downloader) ==", code, name)
                stats.skipped_cached += 1
                results.append({
                    "code": code, "name": name,
                    "status": "szse_index_skip",
                })
                continue

            logger.info("== Index %s (%s) ==", code, name)

            # Debt sector indices (国债指数, 企债指数) track bonds, not stocks,
            # so they don't have meaningful composition data. Skip them —
            # daily index OHLCV data (downloaded by download_csindex.py) is
            # sufficient.  Sector/industry are read from sec_classification.json.
            idx_info = _index_meta.get(code, {})
            sid = idx_info.get("sector_id")
            iid = idx_info.get("industry_id")
            ilabel = idx_info.get("name", code)
            if sid == DEBT_SECTOR_ID or iid in DEBT_SECTOR_INDUSTRY_IDS:
                logger.info("  [debt] %s is a debt sector index (%s), skipping "
                            "composition download (daily index only)", code, ilabel)
                stats.skipped_cached += 1
                results.append({
                    "code": code, "name": name,
                    "status": "debt_index_skip",
                    "sector": sid,
                    "industry": iid,
                })
                continue

            # Check cache & determine if monthly refresh is needed for
            # THIS specific index code.
            cached = find_cached_csv(out_dir, code)
            today_csv = out_dir / f"{code}_closeweight_{today.strftime('%Y%m%d')}.csv"
            monthly_refresh = forced
            if not monthly_refresh and cached:
                m = re.search(r'_closeweight_(\d{8})\.csv$', cached.name)
                if m:
                    cached_month = int(m.group(1)[:6])  # YYYYMM
                    yday_month = yday.year * 100 + yday.month
                    if cached_month < yday_month:
                        monthly_refresh = True

            if monthly_refresh:
                # Monthly refresh mode: skip only if already downloaded TODAY
                if is_valid_file(today_csv, min_bytes=MIN_VALID_BYTES):
                    logger.info("  [cache] %s monthly-refresh but already done today: %s", code, today_csv.name)
                    stats.skipped_cached += 1
                    results.append({"code": code, "name": name, "status": "cached", "file": str(today_csv)})
                    continue
                log_tag = "force-month-start" if forced else "monthly-refresh"
                logger.info("  [%s] %s cache=%s yday_month=%s → refreshing",
                            log_tag, code,
                            cached.name if cached else "(none)",
                            f"{yday.year:04d}{yday.month:02d}")
            elif skip_cached and cached:
                # Normal nightly mode: any valid cache is good enough
                logger.info("  [cache] %s already cached: %s", code, cached.name)
                stats.skipped_cached += 1
                results.append({"code": code, "name": name, "status": "cached", "file": str(cached)})
                continue

            if proxy.is_blocked(CLOSEWEIGHT_URL_TEMPLATE):
                logger.warning("  [host-blocked] OSS bucket blocked, skipping %s", code)
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "blocked"})
                continue

            # Download
            raw = fetch_closeweight_xls(session, code, proxy)
            if raw is None:
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "download_failed"})
                continue

            # Parse
            df = parse_closeweight_xls(raw)
            if df is None:
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "parse_failed"})
                continue

            normalized = normalize_closeweight_df(df)
            if normalized is None or len(normalized) == 0:
                stats.empty += 1
                results.append({"code": code, "name": name, "status": "empty"})
                continue

            # Save CSV. On monthly refresh, stamp the CSV with today's
            # date (overriding the xls's snapshot_date, which is usually the
            # previous business day) so a fresh monthly snapshot flows to
            # prod (stats.sec_composition) under the new month's date.
            if monthly_refresh:
                snapshot_date = today.isoformat()
                normalized["snapshot_date"] = snapshot_date
            else:
                snapshot_date = normalized["snapshot_date"].iloc[0]
            csv_name = csv_filename_for(code, snapshot_date)
            csv_path = out_dir / csv_name
            normalized.to_csv(csv_path, index=False, encoding="utf-8-sig")

            stats.downloaded += 1
            stats.files.append(str(csv_path))
            results.append({
                "code": code,
                "name": name,
                "status": "downloaded",
                "file": str(csv_path),
                "n_constituents": len(normalized),
                "snapshot_date": snapshot_date,
                "monthly_refresh": monthly_refresh,
            })
            logger.info("  [ok] %s: %d constituents, snapshot=%s → %s%s",
                        code, len(normalized), snapshot_date, csv_name,
                        " (monthly stamp)" if monthly_refresh else "")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        n_indices=len(index_codes),
        results=results,
    )
    logger.info(
        "Done CSI index composition. downloaded=%d skipped_cached=%d failed=%d empty=%d",
        stats.downloaded, stats.skipped_cached, stats.failed, stats.empty,
    )
    return summary
