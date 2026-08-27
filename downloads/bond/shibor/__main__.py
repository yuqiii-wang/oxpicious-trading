from __future__ import annotations


import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

from downloads._common import (
    MIN_VALID_BYTES,
    EMPTY_HTML_MAX_BYTES,
    DEFAULT_TIMEOUT,
    COMMON_BASE_HEADERS,
    DEFAULT_START_DATE,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    resolve_out_dir,
    parse_date_window,
    is_valid_file,
    is_error_html,
    safe_write_bytes,
    build_default_session,
    build_chunk_download_plan,
    ChunkDownloadPlanItem,
    scan_present_filenames,
    RunStats,
    convert_xlsx_to_csv,
)


SHIBOR_BASE = "https://www.shibor.sh.cn"

BK_PATH = "/ags/ms/"
DQS_PATH = "/dqs/rest/"
DURL_PATH = "/r/cms/www/chinamoney/data/"

API_CHECK_SHIBOR_HIS = BK_PATH + "cm-u-bk-shibor/ShiborHis"
API_XLSX_SHIBOR_HIS = DQS_PATH + "cm-u-bk-shibor/ShiborHisExcel"

API_CHECK_SHIBOR_PRI = BK_PATH + "cm-u-bk-shibor/ShiborPriHis"
API_XLSX_SHIBOR_PRI = DQS_PATH + "cm-u-bk-shibor/ShiborPriHisExcel"

API_PNL_BK = DURL_PATH + "/shibor/shibor-pnl-bk.json"

API_CHECK_SHIBOR_MN = BK_PATH + "cm-u-bk-shibor/ShiborMnHis"
API_XLSX_SHIBOR_MN = DQS_PATH + "cm-u-bk-shibor/ShiborMnHisExcel"
API_CFG_SHIBOR_MN = BK_PATH + "cm-u-bk-shibor/ShiborMnHisCFG"

API_CHECK_LPR_HIS = BK_PATH + "cm-u-bk-currency/LprHis"
API_XLSX_LPR_HIS = DQS_PATH + "cm-u-bk-currency/LprHisExcel"

T_SHIBOR_HIS = "shibor_his"
T_SHIBOR_PRI = "shibor_pri_his"
T_SHIBOR_MN = "shibor_mn_his"
T_LPR_HIS = "lpr_his"

DATA_TYPES = {
    T_SHIBOR_HIS: {
        "label": "Shibor(上海银行间同业拆放利率)",
        "prefix": "shibor_his",
    },
    T_SHIBOR_PRI: {
        "label": "Shibor报价数据(分机构)",
        "prefix": "shibor_pri_his",
    },
    T_SHIBOR_MN: {
        "label": "Shibor均值数据",
        "prefix": "shibor_mn_his",
    },
    T_LPR_HIS: {
        "label": "贷款市场报价利率(LPR)",
        "prefix": "lpr_his",
    },
}

REFERER_DATASERVICES = "https://www.shibor.sh.cn/shibor/dataservices/"

AJAX_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
AJAX_HEADERS.update(
    {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": SHIBOR_BASE,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": REFERER_DATASERVICES,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
)

FORM_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
FORM_HEADERS.update(
    {
        "Origin": SHIBOR_BASE,
        "Referer": REFERER_DATASERVICES,
        "Content-Type": "application/x-www-form-urlencoded",
        "Upgrade-Insecure-Requests": "1",
    }
)

SHIBOR_TIMEOUT: Tuple[int, int] = (15, 120)
SLEEP_SEC = 5.0
MAX_CHUNK_MONTHS = 12
RE_DATE_RANGE_IN_NAME = re.compile(r"_(\d{8})_(\d{8})\.xlsx$")


@dataclass
class MemberInfo:
    mem_code: str
    instn_cn_nm: str
    instn_en_nm: str


logger = setup_logger("shibor_download")


def build_session() -> requests.Session:
    s = build_default_session()
    return s


def _ymd(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def split_chunks(
    start: date, end: date, months: int = MAX_CHUNK_MONTHS
) -> List[Tuple[date, date]]:
    if end < start:
        return []
    chunks: List[Tuple[date, date]] = []
    cur = start
    while cur <= end:
        y = cur.year + (cur.month - 1 + months) // 12
        m = (cur.month - 1 + months) % 12 + 1
        try:
            nxt = date(y, m, 1) - timedelta(days=1)
        except ValueError:
            nxt = date(y, m, 28)
        chunk_end = min(nxt, end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _fetch_member_info(session: requests.Session, proxy: Optional[AntiBotProxy] = None) -> Optional[MemberInfo]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    resp = proxy.post(
        session,
        SHIBOR_BASE + API_PNL_BK,
        headers=AJAX_HEADERS,
        timeout=SHIBOR_TIMEOUT,
        logger=logger,
        log_tag="[member-info]",
    )
    if resp is None:
        logger.error("Failed to fetch member list (pnl-bk.json): request failed")
        return None

    try:
        data = resp.json()
    except ValueError as e:
        logger.error("Failed to fetch member list (pnl-bk.json): json parse error: %s", e)
        return None

    records = data.get("records") or []
    codes: List[str] = []
    cn_names: List[str] = []
    en_names: List[str] = []
    for v in records:
        mem = v.get("memCode") or ""
        cname = v.get("cname") or ""
        ename = v.get("ename") or ""
        if not mem:
            continue
        codes.append(mem)
        if cname == "汇丰银行":
            cn_names.append(cname + "|汇丰中国")
        else:
            cn_names.append(cname)
        en_names.append(ename)

    if not codes:
        logger.warning("Member list was empty")
        return None

    return MemberInfo(
        mem_code="|" + "|".join(codes),
        instn_cn_nm="|" + "|".join(cn_names),
        instn_en_nm="|" + "|".join(en_names),
    )


def _check_data(
    session: requests.Session,
    data_type: str,
    start: date,
    end: date,
    members: Optional[MemberInfo] = None,
    tendency_value: str = "",
    proxy: Optional[AntiBotProxy] = None,
) -> Tuple[bool, str, bool]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    s_s, s_e = _ymd(start), _ymd(end)
    tag = f"[check {data_type} {s_s}~{s_e}]"

    if data_type == T_SHIBOR_HIS:
        params = {"lang": "cn", "startDate": s_s, "endDate": s_e}
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_CHECK_SHIBOR_HIS,
            params=params,
            headers=AJAX_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    elif data_type == T_SHIBOR_PRI:
        if members is None:
            return False, "member info not available", False
        data = {
            "memCode": members.mem_code,
            "instnCnNm": members.instn_cn_nm,
            "instnEnNm": members.instn_en_nm,
            "lang": "cn",
            "startDate": s_s,
            "endDate": s_e,
        }
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_CHECK_SHIBOR_PRI,
            data=data,
            headers=AJAX_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    elif data_type == T_SHIBOR_MN:
        params = {
            "lang": "cn",
            "startDate": s_s,
            "endDate": s_e,
            "tendencyvalue": tendency_value,
        }
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_CHECK_SHIBOR_MN,
            params=params,
            headers=AJAX_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    elif data_type == T_LPR_HIS:
        params = {"lang": "CN", "startDate": s_s, "endDate": s_e}
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_CHECK_LPR_HIS,
            params=params,
            headers=AJAX_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    else:
        return False, f"unknown data_type: {data_type}", False

    if resp is None:
        return False, "check request failed", False

    try:
        payload = resp.json()
    except ValueError as e:
        return False, f"check json error: {e}", False

    msg = ""
    data_block = payload.get("data")
    if isinstance(data_block, dict):
        msg = (data_block.get("message") or "").strip()
    records = payload.get("records") or []
    if msg:
        logger.warning("Check[%s %s~%s] server message: %s", data_type, s_s, s_e, msg)
    if not records:
        return False, msg or "no records returned", True
    return True, msg or "ok", False


def _download_excel(
    session: requests.Session,
    data_type: str,
    start: date,
    end: date,
    out_file: Path,
    members: Optional[MemberInfo] = None,
    tendency_value: str = "",
    proxy: Optional[AntiBotProxy] = None,
) -> bool:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    s_s, s_e = _ymd(start), _ymd(end)
    tag = f"[download {data_type} {s_s}~{s_e}]"

    if data_type == T_SHIBOR_HIS:
        params = {"lang": "cn", "startDate": s_s, "endDate": s_e}
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_XLSX_SHIBOR_HIS,
            params=params,
            headers=FORM_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    elif data_type == T_SHIBOR_PRI:
        if members is None:
            logger.error("Cannot download pri_his without member info")
            return False
        params = {
            "lang": "cn",
            "startDate": s_s,
            "endDate": s_e,
            "memCode": members.mem_code,
            "instnCnNm": members.instn_cn_nm,
            "instnEnNm": members.instn_en_nm,
        }
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_XLSX_SHIBOR_PRI,
            params=params,
            headers=FORM_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    elif data_type == T_SHIBOR_MN:
        params = {
            "lang": "cn",
            "startDate": s_s,
            "endDate": s_e,
            "tendencyvalue": tendency_value,
        }
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_XLSX_SHIBOR_MN,
            params=params,
            headers=FORM_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    elif data_type == T_LPR_HIS:
        params = {"lang": "CN", "startDate": s_s, "endDate": s_e}
        resp = proxy.post(
            session,
            SHIBOR_BASE + API_XLSX_LPR_HIS,
            params=params,
            headers=FORM_HEADERS,
            timeout=SHIBOR_TIMEOUT,
            logger=logger,
            log_tag=tag,
        )
    else:
        logger.error("Unknown data_type: %s", data_type)
        return False

    if resp is None:
        logger.error("Download xlsx request failed %s %s~%s", data_type, s_s, s_e)
        return False

    ctype = resp.headers.get("Content-Type", "")
    if "html" in ctype.lower() and len(resp.content) < 16 * 1024:
        txt = resp.text[:400]
        if "错误" in txt or "error" in txt.lower():
            logger.warning(
                "Download %s %s~%s appears to be error page (length=%d). snippet: %s",
                data_type, s_s, s_e, len(resp.content), txt[:120],
            )
            return False

    tag = f"[xlsx {data_type} {s_s}~{s_e}]"
    return safe_write_bytes(
        out_file, resp.content,
        min_bytes=MIN_VALID_BYTES, logger=logger, log_tag=tag,
    )


def _chunk_filename(data_type: str, start: date, end: date) -> str:
    prefix = DATA_TYPES[data_type]["prefix"]
    return f"{prefix}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.xlsx"


def download_shibor(
    *,
    out_root: Optional[str] = None,
    start_date: Optional[str] = DEFAULT_START_DATE,
    end_date: Optional[str] = None,
    lookback_years: int = 3,
    data_types: Optional[List[str]] = None,
    chunk_months: int = MAX_CHUNK_MONTHS,
    sleep_sec: float = SLEEP_SEC,
    tendency_value: str = "",
    db_table: str = "stats.debt_identity",
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "shibor", out_root)

    _start, _end = parse_date_window(
        end_date=end_date,
        start_date=start_date,
        lookback_years=lookback_years,
    )

    if data_types is None:
        data_types = [T_SHIBOR_HIS, T_SHIBOR_PRI, T_SHIBOR_MN, T_LPR_HIS]
    for t in data_types:
        if t not in DATA_TYPES:
            raise ValueError(
                f"Unknown data_type: {t}. Valid: {list(DATA_TYPES.keys())}"
            )

    if chunk_months <= 0 or chunk_months > MAX_CHUNK_MONTHS:
        raise ValueError(
            f"chunk_months must be within [1, {MAX_CHUNK_MONTHS}]. "
            f"The SHIBOR engine enforces a ~1 year window per request."
        )

    session = build_session()
    stats = RunStats()
    
    # Create unified AntiBotProxy
    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
    )
    proxy = AntiBotProxy(proxy_config)

    logger.info(
        "Starting SHIBOR download: requested %s -> %s (lookback=%dy). "
        "types=%s, chunk_months=%d, out=%s",
        _start, _end, lookback_years, data_types, chunk_months, out_dir,
    )

    try:
        members: Optional[MemberInfo] = None
        if T_SHIBOR_PRI in data_types:
            members = _fetch_member_info(session, proxy)
            if members is None:
                logger.warning("shibor_pri_his will be skipped (no member list)")
                data_types = [t for t in data_types if t != T_SHIBOR_PRI]

        for dt in data_types:
            logger.info("== Processing data_type %s (%s) ==", dt, DATA_TYPES[dt]["label"])

            if proxy.is_blocked(SHIBOR_BASE):
                logger.warning("  [host-blocked] shibor.sh.cn is blocked, skipping %s", dt)
                stats.failed += len(chunk_plan.items) if 'chunk_plan' in locals() else 1
                continue

            chunks = split_chunks(_start, _end, chunk_months)
            chunks_by_type: Dict[str, List[Tuple[date, date]]] = {dt: chunks}
            type_cfgs: Dict[str, Dict[str, str]] = {dt: {"prefix": DATA_TYPES[dt]["prefix"]}}

            chunk_plan = build_chunk_download_plan(
                out_dir=out_dir,
                chunks_by_type=chunks_by_type,
                type_configs=type_cfgs,
                min_bytes=MIN_VALID_BYTES,
                ext_glob="*.xlsx",
                db_table=db_table,
            )
            stats.skipped_cached += chunk_plan.present_count
            logger.info(
                "  [%s] range %s~%s split into %d chunk(s) (max %d mo each). "
                "cached=%d missing=%d",
                dt, _start, _end, len(chunks), chunk_months,
                chunk_plan.present_count, len(chunk_plan.items),
            )

            citem: ChunkDownloadPlanItem
            for citem in chunk_plan.items:
                if proxy.is_blocked(SHIBOR_BASE):
                    logger.warning("  [host-blocked] shibor.sh.cn blocked, skipping remaining chunks")
                    stats.failed += len(chunk_plan.items) - chunk_plan.items.index(citem)
                    break

                cs, ce = citem.chunk_start, citem.chunk_end
                fname = _chunk_filename(dt, cs, ce)
                fpath = out_dir / fname
                tag = f"[{dt} {_ymd(cs)}~{_ymd(ce)}]"

                if is_valid_file(fpath, min_bytes=MIN_VALID_BYTES):
                    stats.skipped_cached += 1
                    csv_file = fpath.with_suffix(".csv")
                    if is_valid_file(csv_file, min_bytes=MIN_VALID_BYTES):
                        logger.info("%s csv already converted, skipping", tag)
                    else:
                        convert_xlsx_to_csv(fpath, logger=logger, log_tag=tag)
                    proxy.sleep(max(0.1, sleep_sec * 0.3))
                    continue

                # Skip chunks previously confirmed to have no data (empty marker)
                if fpath.exists() and not is_valid_file(fpath, min_bytes=MIN_VALID_BYTES):
                    stats.empty += 1
                    continue

                ok, msg, confirmed_empty = _check_data(session, dt, cs, ce, members=members,
                                      tendency_value=tendency_value,
                                      proxy=proxy)
                if not ok:
                    if confirmed_empty:
                        logger.info("  [empty] %s (%s), writing empty marker", tag, msg)
                        fpath.parent.mkdir(parents=True, exist_ok=True)
                        fpath.write_bytes(b"")
                        stats.empty += 1
                    else:
                        logger.warning("  [check-fail] %s (%s)", tag, msg)
                        stats.failed += 1
                    proxy.sleep(max(0.2, sleep_sec * 0.5))
                    continue

                saved = _download_excel(session, dt, cs, ce, fpath, members=members,
                                        tendency_value=tendency_value,
                                        proxy=proxy)
                if saved:
                    stats.downloaded += 1
                    stats.files.append(str(fpath))
                    # Canonical CSV must exist next to every xlsx — builds
                    # read CSV ONLY (a missing csv is a downloads bug).
                    convert_xlsx_to_csv(fpath, logger=logger, log_tag=tag)
                else:
                    stats.failed += 1

                # Auto-sleep handled by proxy.get()/post()

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        start_date=str(_start),
        end_date=str(_end),
        data_types=data_types,
        chunk_months=chunk_months,
    )
    logger.info(
        "Done SHIBOR download. downloaded=%d skipped(cached)=%d failed=%d empty(check)=%d out=%s",
        stats.downloaded, stats.skipped_cached, stats.failed, stats.empty, out_dir,
    )
    return summary


if __name__ == "__main__":
    print(download_shibor())
