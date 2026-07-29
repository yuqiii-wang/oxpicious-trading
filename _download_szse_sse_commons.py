import time
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from _download_commons import (
    MIN_VALID_BYTES,
    EMPTY_HTML_MAX_BYTES,
    DEFAULT_TIMEOUT,
    COMMON_BASE_HEADERS,
    AntiBotProxy,
    AntiBotConfig,
    build_headers_with_referer,
    resolve_out_dir,
    parse_date_window,
    business_days,
    is_valid_file,
    is_error_html,
    safe_write_bytes,
    build_day_download_plan,
    DayDownloadPlan,
    DayDownloadPlanItem,
    RunStats,
    setup_logger,
    convert_xlsx_to_csv,
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


def download_xlsx_once(
    session: requests.Session,
    params: Dict[str, object],
    headers: Dict[str, str],
    out_file: Path,
    log_tag: str,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    proxy: Optional[AntiBotProxy] = None,
    code_suffix: str = "",
    code_filter: Optional[List[str]] = None,
) -> Optional[Path]:
    if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        logger.info("%s already exists, skipping", log_tag)
        csv_file = out_file.with_suffix(".csv")
        if is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
            logger.info("%s csv already converted, skipping", log_tag)
        else:
            convert_xlsx_to_csv(
                out_file, logger=logger, log_tag=log_tag,
                code_suffix=code_suffix, code_filter=code_filter,
            )
        return out_file

    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))

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
        and is_error_html(content_type, resp.content, max_html_bytes=EMPTY_HTML_MAX_BYTES)
    ):
        logger.warning(
            "%s got html response (no data? length=%d)",
            log_tag, len(resp.content),
        )
        return None

    saved = safe_write_bytes(
        out_file, resp.content,
        min_bytes=MIN_VALID_BYTES, logger=logger, log_tag=log_tag,
        auto_convert=not code_filter,
    )
    if saved and code_filter:
        convert_xlsx_to_csv(
            out_file, logger=logger, log_tag=log_tag,
            code_suffix=code_suffix, code_filter=code_filter,
        )
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
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
    proxy: Optional[AntiBotProxy] = None,
    code_suffix: str = "",
    db_table: Optional[str] = None,
    db_date_column: str = "date",
    db_table_by_type: Optional[Dict[str, str]] = None,
    db_code_suffix: Optional[str] = None,
    code_filter_by_type: Optional[Dict[str, List[str]]] = None,
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

    *db_code_suffix* is forwarded to ``check_identity`` as the ``code_suffix``
    filter so multi-source identity tables can be queried per-exchange
    (e.g. ``code_suffix="SZ"`` for SZSE-only rows).

    *code_filter_by_type* maps a security type (e.g. ``"index"``) to a list
    of bare 6-digit codes to keep when converting the xlsx to CSV. Types
    absent from the mapping (or when the param is None) keep all rows. The
    xlsx is always written in full; only the CSV is filtered.
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
                db_code_suffix=db_code_suffix,
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
            db_code_suffix=db_code_suffix,
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

            if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                stats.skipped_cached += 1
                csv_file = out_file.with_suffix(".csv")
                if is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
                    logger.info("%s csv already converted, skipping", tag)
                else:
                    convert_xlsx_to_csv(
                        out_file, logger=logger, log_tag=tag,
                        code_suffix=code_suffix, code_filter=code_filter,
                    )
                continue

            params = params_builder(item.type_key, item.day)
            path = download_xlsx_once(
                sess, params, headers, out_file, tag,
                proxy=proxy, code_suffix=code_suffix, code_filter=code_filter,
            )
            if path is not None:
                stats.downloaded += 1
                stats.files.append(str(path))
            else:
                if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                    stats.skipped_cached += 1
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
        "Done %s. downloaded=%d skipped=%d failed=%d out=%s",
        banner_label, stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
    )
    return summary
