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


from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Optional, Set

import requests

from _common.pre_check_and_load import check_identity
from downloads._common import (
    DEFAULT_START_DATE,
    MIN_VALID_BYTES,
    EMPTY_HTML_MAX_BYTES,
    AntiBotProxy,
    antibot_config_for,
    RunStats,
    build_headers_with_referer,
    business_days,
    is_valid_file,
    is_error_html,
    parse_date_window,
    resolve_out_dir,
    setup_logger,
    sleep_sec_for,
)


# Pacing (sleep/jitter/timeouts/anti-bot toggles) resolves from this service's
# external_request_config JSON row.
SERVICE = "downloads.options.sse.risk"

SSE_DOWNLOAD_URL = "https://query.sse.com.cn/derivative/downloadRisk.do"
SSE_REFERER = "https://www.sse.com.cn/assortment/options/risk/"

logger = setup_logger("sse_options_risk_download")

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "*/*"})

# The SSE options stream writes stats.options_identity rows intraday, so by
# the evening run every recent trading day is already "in the DB" and the
# identity gate in download_sse_options_risk would skip it — its same-day
# risk CSV would never download (2026-09-29/30 both hit this). The most
# recent RECENT_DAYS trading days always fall through to the local CSV
# cache instead: present files still skip, missing ones download once SSE
# publishes the risk table (same evening, or the following evening at the
# latest on short publication lags).
RECENT_DAYS = 5


def download_options_risk_once(
    session: requests.Session,
    trade_date: date,
    out_file: Path,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Path]:
    if proxy is None:
        proxy = AntiBotProxy(service=SERVICE)
    
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
    sleep_sec: Optional[float] = None,
    session: Optional[requests.Session] = None,
    db_table: str = "stats.options_identity",
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "sse_options_risk", out_root)
    sess = session or requests.Session()

    # Pacing from the service's external_request_config JSON row (sleep_sec,
    # sleep_jitter, timeouts, anti-bot toggles); an explicit sleep_sec
    # argument overrides the row's base sleep only.
    if sleep_sec is None:
        sleep_sec = sleep_sec_for(SERVICE)
    proxy = AntiBotProxy(replace(
        antibot_config_for(SERVICE), base_sleep_sec=sleep_sec,
    ))

    _start, _end = parse_date_window(
        end_date=end_date,
        start_date=start_date,
    )

    days = business_days(_start, _end, reverse=True)
    total_days = len(days)
    # newest trading days bypass the identity gate (see RECENT_DAYS note)
    recent = set(days[:RECENT_DAYS])

    # DB-first: skip dates already present in the options identity table —
    # for the SSE rows only (exchange filter). A date-only check would let
    # SZSE/CFFEX rows mask SSE dates whose risk CSV is still missing
    # (2026-07-18..09-17 all hit this: present via SZSE, risk never fetched).
    # check_identity returns the set of expected trading days that are NOT
    # yet in the DB; dates outside this set are skipped (already built).
    # None = DB unreachable -> the loop falls back to the local CSV cache:
    # dates with a valid file (or a known empty marker) are skipped, the
    # rest download.
    db_missing: Optional[Set[date]] = None
    if db_table:
        try:
            db_missing = check_identity(db_table, _start, _end, exchange="SSE")
        except Exception as e:
            logger.warning(
                "DB identity check failed (%s: %s) — falling back to the "
                "local CSV cache for missing dates", type(e).__name__, e)

    # Bound the download to dates the build can actually build: risk rows
    # are only consumed for dates with a 当日合约 listing CSV
    # (temps/sse_options_price). Without this bound the SSE-exchange gate
    # (DEFAULT_START_DATE 2020) would re-fetch the whole listing-less
    # history whenever those DB rows are purged.
    listing_dir = resolve_out_dir(
        str(Path(__file__).resolve()), "sse_options_price", None)
    listing_dates = {
        date(int(p.stem[-8:-4]), int(p.stem[-4:-2]), int(p.stem[-2:]))
        for p in listing_dir.glob("sse_options_price_*.csv")
        if len(p.stem) >= 8 and p.stem[-8:].isdigit()
    }
    if listing_dates:
        if db_missing is None:
            db_missing = listing_dates & set(days)
        else:
            db_missing &= listing_dates

    stats = RunStats()

    logger.info(
        "Starting SSE options risk download: %s -> %s (%d trading days, %s)",
        _start, _end, total_days,
        f"{len(db_missing)} missing in DB" if db_missing is not None
        else "DB unreachable — CSV cache decides",
    )

    try:
        for idx, d in enumerate(days):
            if proxy.is_blocked(SSE_DOWNLOAD_URL):
                logger.warning("[host-blocked] query.sse.com.cn is blocked, skipping remaining tasks")
                stats.failed += len(days) - idx
                break

            # Skip dates already present in the DB identity table
            # Recent RECENT_DAYS days are exempt — stream-written identity rows
            # would mask the current day's missing risk CSV.
            # (db_missing None = DB unreachable -> the local file checks
            # below decide what's already cached)
            if db_missing is not None and d not in db_missing and d not in recent:
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
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)
    logger.info(download_sse_options_risk())