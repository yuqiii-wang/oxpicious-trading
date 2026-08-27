"""Download Shanghai Stock Exchange (SSE) options risk indicators data.

Studies ``https://www.sse.com.cn/assortment/options/risk/`` and uses the
underlying download API at ``https://query.sse.com.cn/derivative/downloadRisk.do``.

Downloads options risk indicators for all underlying ETFs (510050, 510300, 510500,
588000, 588080) per trade date. The API returns CSV files directly.

Output:
  ``temps/sse_options_risk/sse_options_risk_YYYYMMDD.csv``

Reuses anti-bot machinery (``safe_get``, ``HostStatusTracker``, browser
profile rotation, random sleep) and the download orchestration pattern
from ``_download_szse_sse_commons.py`` and ``_download_commons.py``.
"""

from __future__ import annotations


from datetime import date
from pathlib import Path
from typing import Optional, Set

import requests

from _common.pre_check_and_load import check_identity
from downloads._common import (
    DEFAULT_START_DATE,
    DEFAULT_TIMEOUT,
    MIN_VALID_BYTES,
    EMPTY_HTML_MAX_BYTES,
    AntiBotProxy,
    AntiBotConfig,
    RunStats,
    build_headers_with_referer,
    business_days,
    is_valid_file,
    is_error_html,
    parse_date_window,
    resolve_out_dir,
    setup_logger,
)


SSE_DOWNLOAD_URL = "https://query.sse.com.cn/derivative/downloadRisk.do"
SSE_REFERER = "https://www.sse.com.cn/assortment/options/risk/"

logger = setup_logger("sse_options_risk_download")

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "*/*"})


def download_options_risk_once(
    session: requests.Session,
    trade_date: date,
    out_file: Path,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Path]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    ymd = trade_date.strftime("%Y%m%d")
    params = {
        "trade_date": ymd,
        "productType": "全部",
    }
    log_tag = f"[options_risk {ymd}]"

    resp = proxy.get(
        session,
        SSE_DOWNLOAD_URL,
        params=params,
        headers=SSE_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=log_tag,
    )
    if resp is None:
        logger.error("%s download error: request failed", log_tag)
        return None

    content_type = resp.headers.get("Content-Type", "")
    content_disposition = resp.headers.get("Content-Disposition", "")
    if (
        not content_disposition
        and is_error_html(content_type, resp.content, max_html_bytes=EMPTY_HTML_MAX_BYTES)
    ):
        logger.warning(
            "%s got html response (no data? length=%d), writing empty marker",
            log_tag, len(resp.content),
        )
        # API confirmed no data for this date — write an empty marker file so
        # the next run skips it instead of re-fetching. The caller treats a
        # returned path with a sub-MIN_VALID_BYTES file as an "empty" result.
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"")
        return out_file

    sz = len(resp.content)
    if sz < MIN_VALID_BYTES:
        logger.warning("%s content too small (%d bytes), skipping save", log_tag, sz)
        return None

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "wb") as f:
        f.write(resp.content)

    logger.info("%s saved %s (%d bytes)", log_tag, out_file.name, sz)
    return out_file


def download_sse_options_risk(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
    db_table: str = "stats.options_identity",
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "sse_options_risk", out_root)
    sess = session or requests.Session()

    # Create unified AntiBotProxy
    proxy_config = AntiBotConfig(base_sleep_sec=sleep_sec)
    proxy = AntiBotProxy(proxy_config)

    _start, _end = parse_date_window(
        end_date=end_date,
        start_date=start_date,
    )

    days = business_days(_start, _end, reverse=True)
    total_days = len(days)

    # DB-first: skip dates already present in the options identity table.
    # check_identity returns the set of expected trading days that are NOT
    # yet in the DB; dates outside this set are skipped (already built).
    db_missing: Set[date] = set()
    if db_table:
        # options_identity has no exchange column (PK is date +
        # contract_code), so the check is date-only.
        db_missing = check_identity(db_table, _start, _end)

    stats = RunStats()

    logger.info(
        "Starting SSE options risk download: %s -> %s (%d trading days, %d missing in DB)",
        _start, _end, total_days, len(db_missing) if db_table else total_days,
    )

    try:
        for idx, d in enumerate(days):
            if proxy.is_blocked(SSE_DOWNLOAD_URL):
                logger.warning("[host-blocked] query.sse.com.cn is blocked, skipping remaining tasks")
                stats.failed += len(days) - idx
                break

            # Skip dates already present in the DB identity table
            if db_table and d not in db_missing:
                stats.skipped_cached += 1
                continue

            ymd = d.strftime("%Y%m%d")
            out_file = out_dir / f"sse_options_risk_{ymd}.csv"

            if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                stats.skipped_cached += 1
                continue
            # An existing but sub-threshold file is a no-data marker written
            # by a previous run (API confirmed no data for this date). Skip
            # it so we don't re-fetch dates already known to have no data.
            if out_file.exists():
                stats.empty += 1
                continue

            path = download_options_risk_once(sess, d, out_file, proxy)
            if path is not None and is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                stats.downloaded += 1
                stats.files.append(str(path))
            elif path is not None:
                # API returned no data — empty marker file was written.
                stats.empty += 1
            else:
                stats.failed += 1

            # Auto-sleep handled by proxy.get()/post()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        start_date=str(_start),
        end_date=str(_end),
    )
    logger.info(
        "Done SSE options risk. downloaded=%d skipped=%d empty=%d failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, stats.empty, stats.failed, out_dir,
    )
    return summary


if __name__ == "__main__":
    print(download_sse_options_risk())