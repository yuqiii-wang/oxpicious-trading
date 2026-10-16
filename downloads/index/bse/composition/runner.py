"""Runner for the BSE index composition downloader (北证50 + 北证专精特新).

北证50 (899050) and 北证专精特新 (899601) are published by 北交所 jointly
with 中证指数 (CSI). The index pages' own composition widget
(``/nqhqController/getWeightNotPageList.do`` behind
market_data/bse_indices/bse_bz50.html / bse_zjtx.html) serves only a
10-row live-quote list with NO weights, so the 成分股列表 (constituent +
weight snapshot) comes from the co-published CSI closeweight autofile —
the SAME ``{code}closeweight.xls`` OSS endpoint
``downloads.index.csindex.composition`` downloads. Its fetch/parse/cache
helpers are reused (never duplicated); this module adds only the BSE
scope (``BSE_INDEX_CODES``), the ``.BJ`` stock-code suffix, and its own
external_request_config pacing row (2 requests per monthly pass — the
csindex row's 300s cadence covers a ~500-index sweep, this doesn't need
it).

CSVs land in the SAME ``temps/csi_index_composition`` directory as the
CSI snapshots (``{code}_closeweight_{YYYYMMDD}.csv``) so
``builds.index.composition`` ingests them into stats.sec_composition with
zero build-side changes (source_type='index', stock_code ``920xxx.BJ``,
rank by weight desc).

Refresh semantics mirror the csindex runner: per index, a cached CSV
whose month is before yesterday's month triggers a monthly refresh
(re-download, stamped with today's date); otherwise any valid cache
satisfies the run, and ``--force-month-start`` forces the refresh path on
any day.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from downloads._common import (
    MIN_VALID_BYTES,
    AntiBotProxy,
    RunStats,
    antibot_config_for,
    build_default_session,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
    sleep_sec_for,
)
from downloads._common.exchanges.bjs import BSE_INDEX_CODES
from downloads.index.csindex.composition import (
    CLOSEWEIGHT_URL_TEMPLATE,
    csv_filename_for,
    fetch_closeweight_xls,
    find_cached_csv,
    normalize_closeweight_df,
    parse_closeweight_xls,
)

from ._config import SERVICE, SLEEP_SEC, logger

# Composition rows keep their 北京证券交易所 suffix (add_exchange_suffix maps
# the exchange column "北京证券交易所" → ".BJ"); passed through to the shared
# closeweight normalizer.
ALLOWED_SUFFIXES = (".BJ",)


def download_bse_index_composition(
    *,
    index_codes: Optional[List[str]] = None,
    out_root: Optional[str] = None,
    skip_cached: bool = True,
    sleep_sec: Optional[float] = None,
    force_month_start: bool = False,
) -> Dict[str, Any]:
    """Download the 北证50/北证专精特新 composition (closeweight) snapshots.

    Same monthly-refresh contract as the csindex composition runner (see
    the module docstring); defaults to ``BSE_INDEX_CODES`` and writes into
    the shared CSI composition directory so the index-composition build
    picks the snapshots up.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "csi_index_composition", out_root)

    if not index_codes:
        index_codes = sorted(BSE_INDEX_CODES)

    _index_names = dict(BSE_INDEX_CODES)

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
        "Starting BSE index composition download: %d indices, out=%s",
        len(index_codes), out_dir,
    )

    session = build_default_session()
    stats = RunStats()

    # Pacing from the service's external_request_config JSON row (sleep_sec,
    # sleep_jitter, timeouts, anti-bot toggles); an explicit --sleep-sec
    # overrides the row's base sleep only.
    if sleep_sec is None:
        sleep_sec = SLEEP_SEC
    proxy = AntiBotProxy(replace(
        antibot_config_for(SERVICE), base_sleep_sec=sleep_sec,
    ))

    results: List[Dict[str, Any]] = []

    try:
        for code in index_codes:
            code = str(code).strip()
            if not code:
                continue

            name = _index_names.get(code, code)
            logger.info("== Index %s (%s) ==", code, name)

            # Check cache & determine if monthly refresh is needed for
            # THIS specific index code (same rule as the csindex runner:
            # cached month < yesterday's month → refresh).
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
                    logger.info("  [cache] %s monthly-refresh but already done today: %s",
                                code, today_csv.name)
                    stats.skipped_cached += 1
                    results.append({"code": code, "name": name,
                                    "status": "cached", "file": str(today_csv)})
                    continue
                log_tag = "force-month-start" if forced else "monthly-refresh"
                logger.info("  [%s] %s cache=%s yday_month=%s → refreshing",
                            log_tag, code,
                            cached.name if cached else "(none)",
                            f"{yday.year:04d}{yday.month:02d}")
            elif skip_cached and cached:
                # Normal mode: any valid cache is good enough
                logger.info("  [cache] %s already cached: %s", code, cached.name)
                stats.skipped_cached += 1
                results.append({"code": code, "name": name,
                                "status": "cached", "file": str(cached)})
                continue

            if proxy.is_blocked(CLOSEWEIGHT_URL_TEMPLATE):
                logger.warning("  [host-blocked] OSS bucket blocked, skipping %s", code)
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "blocked"})
                continue

            # Download (CSI autofile endpoint shared with csindex composition)
            raw = fetch_closeweight_xls(session, code, proxy)
            if raw is None:
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "download_failed"})
                continue

            # Parse + normalize with the BSE suffix (920xxx.BJ)
            df = parse_closeweight_xls(raw)
            if df is None:
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "parse_failed"})
                continue

            normalized = normalize_closeweight_df(df, allowed_suffixes=ALLOWED_SUFFIXES)
            if normalized is None or len(normalized) == 0:
                stats.empty += 1
                results.append({"code": code, "name": name, "status": "empty"})
                continue

            # Save CSV. On monthly refresh, stamp the CSV with today's
            # date (overriding the xls's snapshot_date, usually the
            # previous business day) so the new month's snapshot flows to
            # prod (stats.sec_composition) as a fresh (code, date) pair —
            # the csindex runner's convention.
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
        "Done BSE index composition. downloaded=%d skipped_cached=%d failed=%d empty=%d",
        stats.downloaded, stats.skipped_cached, stats.failed, stats.empty,
    )
    return summary
