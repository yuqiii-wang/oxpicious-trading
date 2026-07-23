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
    build_headers_with_referer,
    resolve_out_dir,
    parse_date_window,
    business_days,
    is_valid_file,
    is_error_html,
    safe_write_bytes,
    build_day_download_plan,
    DayDownloadPlanItem,
    RunStats,
    setup_logger,
    convert_xlsx_to_csv,
    safe_get,
    HostStatusTracker,
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
    host_tracker: Optional[HostStatusTracker] = None,
    code_suffix: str = "",
) -> Optional[Path]:
    if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        logger.info("%s already exists, skipping", log_tag)
        csv_file = out_file.with_suffix(".csv")
        if is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
            logger.info("%s csv already converted, skipping", log_tag)
        else:
            convert_xlsx_to_csv(out_file, logger=logger, log_tag=log_tag, code_suffix=code_suffix)
        return out_file

    resp = safe_get(
        session,
        BASE_URL,
        params=params,
        headers=headers,
        timeout=timeout,
        host_tracker=host_tracker,
        anti_bot=True,
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
    sleep_sec: float = 0.8,
    session: Optional[requests.Session] = None,
    host_tracker: Optional[HostStatusTracker] = None,
    code_suffix: str = "",
) -> dict:
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
    )

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
            if host_tracker and host_tracker.is_blocked(BASE_URL):
                logger.warning("  [host-blocked] szse.cn is blocked, skipping remaining tasks")
                stats.failed += len(plan.items) - plan.items.index(item)
                break

            ymd = item.day.strftime("%Y%m%d")
            tag = log_tag_fn(item.type_key, ymd)
            out_file = out_dir / f"{item.prefix}_{ymd}.xlsx"

            if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                stats.skipped_cached += 1
                csv_file = out_file.with_suffix(".csv")
                if is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
                    logger.info("%s csv already converted, skipping", tag)
                else:
                    convert_xlsx_to_csv(out_file, logger=logger, log_tag=tag, code_suffix=code_suffix)
                continue

            params = params_builder(item.type_key, item.day)
            path = download_xlsx_once(sess, params, headers, out_file, tag, host_tracker=host_tracker, code_suffix=code_suffix)
            if path is not None:
                stats.downloaded += 1
                stats.files.append(str(path))
            else:
                if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
                    stats.skipped_cached += 1
                else:
                    stats.failed += 1

            time.sleep(sleep_sec)
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
