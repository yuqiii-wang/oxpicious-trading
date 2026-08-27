"""SZSE downloader runner + SZSE xlsx/CSV format conventions.

SZSE publishes whole-market daily snapshots as xlsx via the ShowReport
API; this module owns the shared download loop (plan -> fetch ->
xlsx→csv canonicalization) and the header-only/empty-marker handling.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    DEFAULT_TIMEOUT,
    EMPTY_MARKER_RETRY_DAYS,
    MIN_VALID_BYTES,
    AntiBotConfig,
    AntiBotProxy,
    DayDownloadPlan,
    DayDownloadPlanItem,
    RunStats,
    build_day_download_plan,
    build_headers_with_referer,
    business_days,
    convert_xlsx_to_csv,
    is_error_html,
    is_valid_file,
    parse_date_window,
    resolve_out_dir,
    safe_write_bytes,
    setup_logger,
)


BASE_URL = "https://www.szse.cn/api/report/ShowReport"

REFERER_ARCHIVE = "https://www.szse.cn/market/trend/archive/index.html"
REFERER_TREND = "https://www.szse.cn/market/trend/index.html"
REFERER_MARGIN = "https://www.szse.cn/disclosure/margin/margin/index.html"


def build_headers(referer: str) -> Dict[str, str]:
    return build_headers_with_referer(referer)


logger = setup_logger("szse_download")


ParamsBuilder = Callable[[str, date], Dict[str, object]]
LogTagFn = Callable[[str, str], str]


def _csv_has_data(csv_path: Path) -> bool:
    """Check if a CSV file has actual data rows (not just a header or a
    "no data" placeholder).

    Used to detect header-only xlsx exports that the SZSE server returns
    as valid xlsx (>= 1024 bytes) but contain no actual data rows — only
    the column header, or a single row with "没有找到符合条件的数据！" (no
    matching data found). Without this check, such files are treated as
    "already downloaded" and block re-downloading on subsequent runs.
    """
    try:
        if not csv_path.exists() or not csv_path.is_file():
            return False
        with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.readlines()
        if len(lines) < 2:
            return False
        # Check each non-header row for actual data. A real data row has
        # multiple non-empty fields (e.g. date + code + OHLC). The SZSE
        # "no data" placeholder has only one field filled ("没有找到符合条件的数据！").
        for line in lines[1:]:
            parts = line.split(",")
            # Count non-empty fields (stripped)
            non_empty = sum(1 for p in parts if p.strip())
            # A real data row has at least 3 non-empty fields (date, code, name)
            # The "no data" message has only 1 non-empty field
            if non_empty >= 3:
                return True
        return False
    except (OSError, UnicodeDecodeError):
        return False


def _write_empty_markers(out_file: Path) -> None:
    """Write 0-byte xlsx and csv markers so the date is skipped on next run."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(b"")
    csv_marker = out_file.with_suffix(".csv")
    csv_marker.write_bytes(b"")


def _default_sec_type(type_key: str) -> str:
    """Map a security type key to a canonical sec_type for the CSV.

    stock/etf/index keys map to themselves (single-type exports); anything
    else (margin detail/summary, options, ...) is mixed/other -> "auto"
    (per-row prefix inference).
    """
    return type_key if type_key in ("stock", "etf", "index") else "auto"


def download_xlsx_once(
    session: requests.Session,
    params: Dict[str, object],
    headers: Dict[str, str],
    out_file: Path,
    log_tag: str,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    proxy: Optional[AntiBotProxy] = None,
    exchange: str = "",
    code_filter: Optional[List[str]] = None,
    sec_type: str = "auto",
) -> Optional[Path]:
    if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        logger.info("%s already exists, skipping", log_tag)
        csv_file = out_file.with_suffix(".csv")
        if is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
            logger.info("%s csv already converted, skipping", log_tag)
        else:
            convert_xlsx_to_csv(
                out_file, logger=logger, log_tag=log_tag,
                exchange=exchange, code_filter=code_filter,
                sec_type=sec_type,
            )
        # Detect header-only xlsx (valid ZIP, no data rows). The SZSE
        # archive endpoint returns such xlsx for dates it has no data for.
        # Treat as empty so the caller can try a fallback source.
        if not _csv_has_data(csv_file):
            logger.warning(
                "%s xlsx has header only (no data rows), treating as empty",
                log_tag,
            )
            out_file.unlink(missing_ok=True)
            csv_file.unlink(missing_ok=True)
            _write_empty_markers(out_file)
            return None
        return out_file

    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    resp = proxy.get(
        session,
        BASE_URL,
        params=params,
        headers=headers,
        timeout=timeout,
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
        and is_error_html(content_type, resp.content)
    ):
        logger.warning(
            "%s got html response (no data? length=%d), writing empty marker",
            log_tag, len(resp.content),
        )
        _write_empty_markers(out_file)
        return None

    saved = safe_write_bytes(
        out_file, resp.content,
        min_bytes=MIN_VALID_BYTES, logger=logger, log_tag=log_tag,
        auto_convert=not code_filter,
        exchange=exchange, sec_type=sec_type,
    )
    if saved and code_filter:
        convert_xlsx_to_csv(
            out_file, logger=logger, log_tag=log_tag,
            exchange=exchange, code_filter=code_filter,
            sec_type=sec_type,
        )
    if saved:
        # Detect header-only xlsx after fresh download (same as above).
        csv_file = out_file.with_suffix(".csv")
        if not _csv_has_data(csv_file):
            logger.warning(
                "%s downloaded xlsx has header only (no data rows), "
                "treating as empty",
                log_tag,
            )
            out_file.unlink(missing_ok=True)
            csv_file.unlink(missing_ok=True)
            _write_empty_markers(out_file)
            return None
    return out_file if saved else None


def run_szse_download(
    *,
    caller_file: str,
    out_dirname: str,
    banner_label: str,
    security_cfgs: Dict[str, Dict[str, str]],
    headers: Dict[str, str],
    params_builder: ParamsBuilder,
    log_tag_fn: LogTagFn,
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str,
    security_types: Optional[List[str]] = None,
    sleep_sec: float = DEFAULT_SLEEP_SEC,
    session: Optional[requests.Session] = None,
    proxy: Optional[AntiBotProxy] = None,
    exchange: str = "",
    db_table: Optional[str] = None,
    db_date_column: str = "date",
    db_table_by_type: Optional[Dict[str, str]] = None,
    db_exchange: Optional[str] = None,
    code_filter_by_type: Optional[Dict[str, List[str]]] = None,
    sec_type_by_type: Optional[Dict[str, str]] = None,
    skip_empty_markers: bool = False,
    fallback_params_builder: Optional[ParamsBuilder] = None,
    fallback_headers: Optional[Dict[str, str]] = None,
) -> dict:
    """Run a SZSE download loop.

    When *db_table* is provided, the download plan is built from DB state
    (dates already present in the DB table are skipped) instead of scanning
    the local filesystem for cached xlsx files.

    *db_table_by_type* overrides *db_table* on a per-security-type basis —
    useful when each type (stock/etf/option) feeds a different identity
    table (e.g. ``stats.stock_identity`` vs ``stats.etf_identity``). When
    both are given, *db_table_by_type* wins for types it covers; *db_table*
    is used as a fallback for types not in the mapping.

    *db_exchange* is forwarded to ``check_identity`` as the ``exchange``
    filter so multi-source identity tables can be queried per-exchange
    (e.g. ``db_exchange="SZ"`` for SZSE-only rows).

    *code_filter_by_type* maps a security type (e.g. ``"index"``) to a list
    of bare 6-digit codes to keep when converting the xlsx to CSV. Types
    absent from the mapping (or when the param is None) keep all rows. The
    xlsx is always written in full; only the CSV is filtered.

    *sec_type_by_type* overrides the default security-type -> sec_type
    mapping used when canonicalizing the CSV (stock/etf/index keys map to
    themselves; margin detail/summary and options fall back to "auto"
    per-row prefix inference).

    *skip_empty_markers* — when True, dates that have a local 0-byte CSV
    marker (created when a previous fetch returned no data) are also
    excluded from the download plan, preventing re-downloading dates
    already confirmed to have no data. Works in both DB-first and
    filesystem-scan modes.

    *fallback_params_builder* / *fallback_headers* — when the primary
    download returns no data (header-only xlsx or HTML error), the loop
    retries with the fallback params/headers. Used by the archive script
    to fall back to the trend endpoint when the archive endpoint has no
    data for a given date. Files/markers from the primary attempt are
    cleaned up before the fallback runs.
    """
    out_dir = resolve_out_dir(caller_file, out_dirname, out_root)

    _start, _end = parse_date_window(
        end_date=end_date,
        start_date=start_date,
    )

    if security_types is None:
        security_types = list(security_cfgs.keys())

    for st in security_types:
        if st not in security_cfgs:
            raise ValueError(
                f"Unknown security_type: {st}. "
                f"Valid: {list(security_cfgs.keys())}"
            )

    # Build the per-day plan. When db_table_by_type is provided, each type
    # gets its own check_identity query against its own identity table;
    # otherwise all types share a single db_table (or a filesystem scan).
    if db_table_by_type:
        plan = DayDownloadPlan()
        for st in security_types:
            tbl = db_table_by_type.get(st, db_table)
            single_type_cfgs = {st: {"prefix": security_cfgs[st]["prefix"]}}
            sub_plan = build_day_download_plan(
                out_dir=out_dir,
                start_date=_start,
                end_date=_end,
                type_configs=single_type_cfgs,
                min_bytes=MIN_VALID_BYTES,
                weekdays_only=True,
                sort_newest_first=True,
                ext_glob="*.xlsx",
                db_table=tbl,
                db_date_column=db_date_column,
                db_exchange=db_exchange,
                skip_empty_markers=skip_empty_markers,
            )
            plan.items.extend(sub_plan.items)
            plan.present_count += sub_plan.present_count
            plan.total_expected += sub_plan.total_expected
    else:
        type_configs_for_plan: Dict[str, Dict[str, str]] = {
            st: {"prefix": security_cfgs[st]["prefix"]}
            for st in security_types
        }
        plan = build_day_download_plan(
            out_dir=out_dir,
            start_date=_start,
            end_date=_end,
            type_configs=type_configs_for_plan,
            min_bytes=MIN_VALID_BYTES,
            weekdays_only=True,
            sort_newest_first=True,
            ext_glob="*.xlsx",
            db_table=db_table,
            db_date_column=db_date_column,
            db_exchange=db_exchange,
            skip_empty_markers=skip_empty_markers,
        )

    # Create proxy if not provided
    if proxy is None:
        proxy_config = AntiBotConfig(
            base_sleep_sec=sleep_sec,
        )
        proxy = AntiBotProxy(proxy_config)

    sess = session or requests.Session()
    stats = RunStats(skipped_cached=plan.present_count)

    total_counted = len(business_days(_start, _end, reverse=True))
    logger.info(
        "Starting SZSE %s download: %s -> %s (%d weekdays). types=%s. %s",
        banner_label, _start, _end, total_counted, security_types, plan.summary_str(),
    )

    try:
        item: DayDownloadPlanItem
        for item in plan.items:
            if proxy.is_blocked(BASE_URL):
                logger.warning("  [host-blocked] szse.cn is blocked, skipping remaining tasks")
                stats.failed += len(plan.items) - plan.items.index(item)
                break

            ymd = item.day.strftime("%Y%m%d")
            tag = log_tag_fn(item.type_key, ymd)
            out_file = out_dir / f"{item.prefix}_{ymd}.xlsx"
            code_filter = (
                code_filter_by_type.get(item.type_key)
                if code_filter_by_type is not None
                else None
            )
            item_sec_type = (
                sec_type_by_type.get(item.type_key)
                if sec_type_by_type is not None
                else None
            ) or _default_sec_type(item.type_key)

            if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                csv_file = out_file.with_suffix(".csv")
                if not is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
                    convert_xlsx_to_csv(
                        out_file, logger=logger, log_tag=tag,
                        exchange=exchange, code_filter=code_filter,
                        sec_type=item_sec_type,
                    )
                if _csv_has_data(csv_file):
                    stats.skipped_cached += 1
                    continue
                # Header-only xlsx — clean up and fall through to re-download
                # (or try fallback if configured).
                logger.info("%s cached xlsx has no data rows, will re-download", tag)
                out_file.unlink(missing_ok=True)
                csv_file.unlink(missing_ok=True)

            # Skip dates previously confirmed to have no data (empty marker).
            # Recent markers (last EMPTY_MARKER_RETRY_DAYS calendar days) are
            # RETRIED instead: a "no data" response on a recent trading day
            # usually just means the export wasn't published yet when we first
            # asked (intraday runs) — a permanent marker would leave the date
            # stuck empty forever, so different securities end up with
            # different latest loaded dates.
            if out_file.exists():
                marker_age = (date.today() - item.day).days
                if marker_age > EMPTY_MARKER_RETRY_DAYS:
                    stats.empty += 1
                    continue
                logger.info(
                    "%s empty marker is recent (%dd old), retrying download",
                    tag, marker_age,
                )
                out_file.unlink(missing_ok=True)
                out_file.with_suffix(".csv").unlink(missing_ok=True)

            params = params_builder(item.type_key, item.day)
            path = download_xlsx_once(
                sess, params, headers, out_file, tag,
                proxy=proxy, exchange=exchange, code_filter=code_filter,
                sec_type=item_sec_type,
            )
            if path is None and fallback_params_builder is not None:
                # Primary returned no data — clean up any markers/files and
                # try the fallback source (e.g. trend endpoint when archive
                # has no data for this date).
                out_file.unlink(missing_ok=True)
                out_file.with_suffix(".csv").unlink(missing_ok=True)
                fb_params = fallback_params_builder(item.type_key, item.day)
                fb_tag = f"{tag}[fallback]"
                logger.info("%s primary no data, trying fallback", tag)
                path = download_xlsx_once(
                    sess, fb_params,
                    fallback_headers if fallback_headers is not None else headers,
                    out_file, fb_tag,
                    proxy=proxy, exchange=exchange, code_filter=code_filter,
                    sec_type=item_sec_type,
                )

            if path is not None:
                stats.downloaded += 1
                stats.files.append(str(path))
            else:
                if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                    stats.skipped_cached += 1
                elif out_file.exists():
                    stats.empty += 1
                else:
                    stats.failed += 1
            # Auto-sleep is handled by proxy.get() inside download_xlsx_once
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        start_date=str(_start),
        end_date=str(_end),
    )
    logger.info(
        "Done %s. downloaded=%d skipped=%d failed=%d empty=%d out=%s",
        banner_label, stats.downloaded, stats.skipped_cached, stats.failed, stats.empty, out_dir,
    )
    return summary
