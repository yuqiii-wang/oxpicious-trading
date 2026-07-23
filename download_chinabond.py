from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from _download_commons import (
    MIN_VALID_BYTES,
    EMPTY_HTML_MAX_BYTES,
    DEFAULT_TIMEOUT,
    COMMON_BASE_HEADERS,
    setup_logger,
    resolve_out_dir,
    parse_date_window,
    is_valid_file,
    is_error_html,
    safe_write_bytes,
    build_default_session,
    build_day_download_plan,
    build_year_download_plan,
    DayDownloadPlanItem,
    YearDownloadPlanItem,
    RunStats,
    business_days,
    convert_xlsx_to_csv,
    safe_get,
    safe_post,
    HostStatusTracker,
)


CHINABOND_BASE = "https://yield.chinabond.com.cn"
YIELD_MAIN = CHINABOND_BASE + "/cbweb-mn/yield_main?locale=zh_CN"
API_QUERY_TREE = CHINABOND_BASE + "/cbweb-mn/yc/queryTree?locale=zh_CN"
API_DOWN_BZQX_DETAIL = CHINABOND_BASE + "/cbweb-mn/yc/downBzqxDetail"
API_DOWN_YEAR_BZQX_LIST = CHINABOND_BASE + "/cbweb-mn/yc/downYearBzqxList"
API_DOWN_YEAR_BZQX = CHINABOND_BASE + "/cbweb-mn/yc/downYearBzqx"

YC_TREASURY_BOND = "treasury_bond"

YC_CFG_BUILTIN: Dict[str, Dict[str, str]] = {
    YC_TREASURY_BOND: {
        "label": "中债国债收益率曲线",
        "ycDefId": "2c9081e50a2f9606010a3068cae70001",
        "parent_name": "中债国债曲线",
    },
}

CHINABOND_TIMEOUT: Tuple[int, int] = (20, 180)
SLEEP_SEC = 1.5

CHINABOND_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
CHINABOND_HEADERS["Referer"] = YIELD_MAIN

RE_YEAR_IN_NAME = re.compile(r"_(\d{4})\.xlsx$")
RE_DATE_IN_NAME = re.compile(r"_(\d{8})\.xlsx$")


for _k in YC_CFG_BUILTIN:
    safe = _k.lower().replace(" ", "_")
    YC_CFG_BUILTIN[_k].setdefault("prefix", f"chinabond_bzqx_{safe}")


logger = setup_logger("chinabond_download")


def build_session() -> requests.Session:
    s = build_default_session()
    s.headers.update(CHINABOND_HEADERS)
    return s


def bootstrap_session(session: requests.Session, host_tracker: Optional[HostStatusTracker] = None) -> bool:
    resp = safe_get(
        session,
        YIELD_MAIN,
        timeout=CHINABOND_TIMEOUT,
        host_tracker=host_tracker,
        anti_bot=True,
        logger=logger,
        log_tag="[bootstrap]",
    )
    if resp is None:
        logger.error("Failed to bootstrap chinabond session: request failed")
        return False
    return True


def fetch_yc_tree(session: requests.Session, host_tracker: Optional[HostStatusTracker] = None) -> list:
    resp = safe_post(
        session,
        API_QUERY_TREE,
        timeout=CHINABOND_TIMEOUT,
        host_tracker=host_tracker,
        anti_bot=True,
        logger=logger,
        log_tag="[fetch-yc-tree]",
    )
    if resp is None:
        logger.warning("Failed to fetch queryTree, using builtin ids: request failed")
        return []

    try:
        return resp.json()
    except ValueError as e:
        logger.warning("Failed to fetch queryTree, using builtin ids: json parse error: %s", e)
        return []


def resolve_yc_defid(session: requests.Session, curve_key: str, host_tracker: Optional[HostStatusTracker] = None) -> Optional[str]:
    if curve_key in YC_CFG_BUILTIN and YC_CFG_BUILTIN[curve_key].get("ycDefId"):
        return YC_CFG_BUILTIN[curve_key]["ycDefId"]
    tree = fetch_yc_tree(session, host_tracker)
    if not tree:
        return None
    if curve_key not in YC_CFG_BUILTIN:
        return None
    cfg = YC_CFG_BUILTIN[curve_key]
    target_name = cfg["label"]
    for node in tree:
        if node.get("name") == target_name:
            yid = node.get("id")
            if yid:
                cfg["ycDefId"] = yid
                return yid
    return None


def download_day_bzqx(
    session: requests.Session,
    yc_defid: str,
    work_date: date,
    out_file: Path,
    host_tracker: Optional[HostStatusTracker] = None,
) -> bool:
    params = {
        "ycDefIds": yc_defid,
        "zblx": "txy",
        "workTime": work_date.strftime("%Y-%m-%d"),
        "dxbj": "0",
        "qxlx": "0",
        "yqqxN": "N",
        "yqqxK": "K",
        "wrjxCBFlag": "0",
        "locale": "zh_CN",
    }
    tag = f"[day {work_date}]"

    resp = safe_get(
        session,
        API_DOWN_BZQX_DETAIL,
        params=params,
        timeout=CHINABOND_TIMEOUT,
        host_tracker=host_tracker,
        anti_bot=True,
        logger=logger,
        log_tag=tag,
    )
    if resp is None:
        logger.error("  [day-download-http] %s: request failed", work_date)
        return False

    ctype = resp.headers.get("Content-Type", "")
    if is_error_html(ctype, resp.content, max_html_bytes=EMPTY_HTML_MAX_BYTES):
        logger.warning(
            "  [day-empty] %s (html error page, len=%d)", work_date, len(resp.content)
        )
        return False

    return safe_write_bytes(
        out_file, resp.content,
        min_bytes=MIN_VALID_BYTES, logger=logger, log_tag=tag,
    )


def download_year_bzqx(
    session: requests.Session,
    yc_defid: str,
    year: int,
    out_file: Path,
    host_tracker: Optional[HostStatusTracker] = None,
) -> bool:
    params = (
        f"year={year}&&wrjxCBFlag=0&&zblx=txy&&"
        f"ycDefId={yc_defid}&&locale=zh_CN"
    )
    url = API_DOWN_YEAR_BZQX + "?" + params
    tag = f"[year {year}]"

    resp = safe_get(
        session,
        url,
        timeout=CHINABOND_TIMEOUT,
        host_tracker=host_tracker,
        anti_bot=True,
        logger=logger,
        log_tag=tag,
    )
    if resp is None:
        logger.error("  [year-download-http] %d: request failed", year)
        return False

    ctype = resp.headers.get("Content-Type", "")
    if is_error_html(ctype, resp.content, max_html_bytes=EMPTY_HTML_MAX_BYTES):
        logger.warning(
            "  [year-empty] %d (html error page, len=%d)", year, len(resp.content)
        )
        return False

    return safe_write_bytes(
        out_file, resp.content,
        min_bytes=MIN_VALID_BYTES, logger=logger, log_tag=tag,
    )


def _year_filename(curve_key: str, year: int) -> str:
    prefix = YC_CFG_BUILTIN[curve_key].get("prefix") or "chinabond_bzqx_year"
    return f"{prefix}_{year}.xlsx"


def _day_filename(curve_key: str, d: date) -> str:
    prefix = YC_CFG_BUILTIN[curve_key].get("prefix") or "chinabond_bzqx_day"
    return f"{prefix}_{d.strftime('%Y%m%d')}.xlsx"


def download_chinabond(
    *,
    out_root: Optional[str] = None,
    mode: str = "year",
    start_date: Optional[str] = "2021-01-01",
    end_date: Optional[str] = None,
    lookback_years: int = 3,
    curve_keys: Optional[List[str]] = None,
    sleep_sec: float = SLEEP_SEC,
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "chinabond", out_root)

    if mode not in ("year", "day"):
        raise ValueError(f"mode must be 'year' or 'day', got: {mode}")

    if curve_keys is None:
        curve_keys = [YC_TREASURY_BOND]
    for k in curve_keys:
        if k not in YC_CFG_BUILTIN:
            raise ValueError(
                f"Unknown curve_key: {k}. Valid: {list(YC_CFG_BUILTIN.keys())}"
            )

    today = date.today()
    _start, _end = parse_date_window(
        end_date=end_date,
        start_date=start_date,
        lookback_years=lookback_years,
    )

    logger.info(
        "Starting chinabond download: mode=%s window=%s->%s (lookback=%dy) "
        "curves=%s out=%s",
        mode, _start, _end, lookback_years, curve_keys, out_dir,
    )

    session = build_session()
    host_tracker = HostStatusTracker()

    if not bootstrap_session(session, host_tracker):
        return {"error": "bootstrap failed", "out_dir": str(out_dir)}

    stats = RunStats()

    try:
        for ck in curve_keys:
            if host_tracker.is_blocked(CHINABOND_BASE):
                logger.warning("  [host-blocked] yield.chinabond.com.cn is blocked, skipping %s", ck)
                stats.failed += 1
                continue

            yc_defid = resolve_yc_defid(session, ck, host_tracker)
            if not yc_defid:
                logger.error(
                    "[%s] cannot resolve ycDefId, skipping curve", ck,
                )
                stats.failed += 1
                continue
            cfg = YC_CFG_BUILTIN[ck]
            logger.info(
                "== Curve %s (%s) ycDefId=%s ==", ck, cfg["label"], yc_defid,
            )

            if mode == "year":
                always_refresh: set = set()
                if _end.year == today.year:
                    always_refresh = {today.year}
                    logger.info(
                        "  Will re-download partial current year %d even if cached",
                        today.year,
                    )

                year_plan = build_year_download_plan(
                    out_dir=out_dir,
                    start_date=_start,
                    end_date=_end,
                    type_configs={ck: {"prefix": cfg["prefix"]}},
                    min_bytes=MIN_VALID_BYTES,
                    always_refresh_years=always_refresh,
                    ext_glob="*.xlsx",
                )
                stats.skipped_cached += year_plan.present_count

                yitem: YearDownloadPlanItem
                for yitem in year_plan.items:
                    if host_tracker.is_blocked(CHINABOND_BASE):
                        logger.warning("  [host-blocked] yield.chinabond.com.cn blocked, skipping remaining years")
                        stats.failed += len(year_plan.items) - year_plan.items.index(yitem)
                        break

                    fpath = out_dir / _year_filename(ck, yitem.year)
                    ok = download_year_bzqx(session, yc_defid, yitem.year, fpath, host_tracker)
                    if ok:
                        if is_valid_file(fpath, min_bytes=MIN_VALID_BYTES):
                            stats.files.append(str(fpath))
                        stats.downloaded += 1
                    else:
                        if is_valid_file(fpath, min_bytes=MIN_VALID_BYTES):
                            stats.skipped_cached += 1
                        else:
                            stats.empty += 1
                    time.sleep(sleep_sec)

            else:
                type_cfg = {ck: {"prefix": cfg["prefix"]}}
                day_plan = build_day_download_plan(
                    out_dir=out_dir,
                    start_date=_start,
                    end_date=_end,
                    type_configs=type_cfg,
                    min_bytes=MIN_VALID_BYTES,
                    weekdays_only=True,
                    sort_newest_first=False,
                    ext_glob="*.xlsx",
                )
                stats.skipped_cached += day_plan.present_count
                logger.info(
                    "  [%s] %s", ck, day_plan.summary_str(),
                )

                ditem: DayDownloadPlanItem
                for ditem in day_plan.items:
                    if host_tracker.is_blocked(CHINABOND_BASE):
                        logger.warning("  [host-blocked] yield.chinabond.com.cn blocked, skipping remaining days")
                        stats.failed += len(day_plan.items) - day_plan.items.index(ditem)
                        break

                    fpath = out_dir / _day_filename(ck, ditem.day)
                    ok = download_day_bzqx(session, yc_defid, ditem.day, fpath, host_tracker)
                    if ok:
                        if is_valid_file(fpath, min_bytes=MIN_VALID_BYTES):
                            stats.files.append(str(fpath))
                        stats.downloaded += 1
                    else:
                        if is_valid_file(fpath, min_bytes=MIN_VALID_BYTES):
                            stats.skipped_cached += 1
                        else:
                            stats.empty += 1
                    time.sleep(sleep_sec)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        mode=mode,
        out_dir=str(out_dir),
        start_date=str(_start),
        end_date=str(_end),
        curve_keys=curve_keys,
    )
    logger.info(
        "Done chinabond mode=%s. downloaded=%d skipped(cached)=%d failed=%d empty=%d out=%s",
        mode, stats.downloaded, stats.skipped_cached, stats.failed, stats.empty, out_dir,
    )
    return summary


if __name__ == "__main__":
    print(download_chinabond())
