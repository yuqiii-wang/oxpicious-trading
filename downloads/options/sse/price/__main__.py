"""Download Shanghai Stock Exchange (SSE) options daily contract price data.

Studies ``https://www.sse.com.cn/assortment/options/disclo/preinfo/`` and uses the
underlying JSONP API at ``https://query.sse.com.cn/commonQuery.do``.

Downloads options daily contract data (当日合约) for all underlying ETFs.
IMPORTANT: The API always returns the current trading day's data only.
Historical backfill is not supported by this endpoint.

Output:
  ``temps/sse_options_price/sse_options_price_YYYYMMDD.csv``

Reuses anti-bot machinery (``safe_post``, ``HostStatusTracker``, browser
profile rotation, random sleep) from ``_download_commons.py``.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

import requests

import pandas as pd

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    MIN_VALID_BYTES,
    AntiBotProxy,
    AntiBotConfig,
    RunStats,
    build_headers_with_referer,
    is_valid_file,
    last_business_day,
    resolve_out_dir,
    setup_logger,
)


SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_REFERER = "https://www.sse.com.cn/assortment/options/disclo/preinfo/"

logger = setup_logger("sse_options_price_download")

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "*/*"})

JSONP_CALLBACK_RE = re.compile(r"jsonpCallback\d*\((.*)\)", re.DOTALL)


def parse_jsonp_response(content: bytes) -> Optional[dict]:
    try:
        text = content.decode("utf-8", errors="ignore")
        match = JSONP_CALLBACK_RE.match(text)
        if not match:
            logger.debug("JSONP regex no match. Content preview: %s", text[:200])
            return None
        json_str = match.group(1)
        return json.loads(json_str)
    except Exception as e:
        logger.debug("JSONP parse error: %s. Content preview: %s", e, content[:200])
        return None


def download_options_price_once(
    session: requests.Session,
    trade_date: date,
    out_file: Path,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Path]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))

    ymd = trade_date.strftime("%Y%m%d")
    log_tag = f"[options_price {ymd}]"

    all_results = []
    page_no = 1
    page_size = 100

    while True:
        post_data = {
            "jsonCallBack": "jsonpCallback",
            "isPagination": "true",
            "expireDate": "",
            "securityId": "",
            "sqlId": "SSE_ZQPZ_YSP_GGQQZSXT_XXPL_DRHY_SEARCH_L",
            "pageHelp.pageSize": page_size,
            "pageHelp.pageNo": page_no,
            "pageHelp.beginPage": page_no,
            "pageHelp.endPage": page_no,
            "pageHelp.cacheSize": 1,
        }

        resp = proxy.post(
            session,
            SSE_QUERY_URL,
            data=post_data,
            headers=SSE_HEADERS,
            timeout=DEFAULT_TIMEOUT,
            logger=logger,
            log_tag=log_tag,
        )
        if resp is None:
            logger.error("%s download error: request failed", log_tag)
            return None

        parsed_data = parse_jsonp_response(resp.content)
        if parsed_data is None:
            logger.warning("%s failed to parse JSONP response", log_tag)
            return None

        results = parsed_data.get("result", [])
        if not results:
            break

        all_results.extend(results)

        page_info = parsed_data.get("pageHelp", {})
        total_pages = page_info.get("pageCount", 1)
        if page_no >= total_pages:
            break

        page_no += 1
        proxy.sleep(0.2)

    if not all_results:
        logger.warning("%s no data returned", log_tag)
        return None

    df = pd.DataFrame(all_results)

    df = df.rename(columns={
        "SECURITY_ID": "合约编码",
        "CONTRACT_ID": "合约交易代码",
        "CONTRACT_SYMBOL": "合约简称",
        "SECURITYNAMEBYID": "标的券名称及代码",
        "CALL_OR_PUT": "类型",
        "EXERCISE_PRICE": "行权价",
        "CONTRACT_UNIT": "合约单位",
        "EXERCISE_DATE": "期权行权日",
        "DELIVERY_DATE": "行权交收日",
        "EXPIRE_DATE": "到期日",
        "CONTRACTFLAG": "新挂",
        "DAILY_PRICE_UPLIMIT": "涨停价格",
        "DAILY_PRICE_DOWNLIMIT": "跌停价格",
        "SETTL_PRICE": "前结算价",
        "CHANGEFLAG": "调整",
        "DELISTFLAG": "停牌",
    })

    df["交易日期"] = trade_date.strftime("%Y-%m-%d")

    columns_order = [
        "合约编码",
        "合约交易代码",
        "合约简称",
        "标的券名称及代码",
        "类型",
        "行权价",
        "合约单位",
        "期权行权日",
        "行权交收日",
        "到期日",
        "新挂",
        "涨停价格",
        "跌停价格",
        "前结算价",
        "调整",
        "停牌",
        "交易日期",
    ]

    df = df[columns_order]

    out_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_file, index=False, encoding="utf-8-sig")

    sz = out_file.stat().st_size
    logger.info("%s saved %s (%d rows, %d bytes)", log_tag, out_file.name, len(df), sz)
    return out_file


def download_sse_options_price(
    out_root: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "sse_options_price", out_root)
    sess = session or requests.Session()
    
    # Create unified AntiBotProxy
    proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))

    trade_date = last_business_day()
    ymd = trade_date.strftime("%Y%m%d")
    out_file = out_dir / f"sse_options_price_{ymd}.csv"

    stats = RunStats()

    logger.info(
        "Starting SSE options price download for %s",
        trade_date,
    )

    if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        logger.info("%s already cached, skipping download", out_file.name)
        stats.skipped_cached += 1
        summary = stats.to_dict(
            out_dir=str(out_dir),
            trade_date=str(trade_date),
        )
        logger.info(
            "Done SSE options price. downloaded=%d skipped=%d failed=%d out=%s",
            stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
        )
        return summary

    if proxy.is_blocked(SSE_QUERY_URL):
        logger.warning("[host-blocked] query.sse.com.cn is blocked, cannot download")
        stats.failed += 1
        summary = stats.to_dict(
            out_dir=str(out_dir),
            trade_date=str(trade_date),
        )
        logger.info(
            "Done SSE options price. downloaded=%d skipped=%d failed=%d out=%s",
            stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
        )
        return summary

    path = download_options_price_once(sess, trade_date, out_file, proxy)
    if path is not None:
        stats.downloaded += 1
        stats.files.append(str(path))
    else:
        stats.failed += 1

    summary = stats.to_dict(
        out_dir=str(out_dir),
        trade_date=str(trade_date),
    )
    logger.info(
        "Done SSE options price. downloaded=%d skipped=%d failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description="Download SSE options price data")
    parser.add_argument("--out_root", type=str, default=None, help="Output directory root")
    args = parser.parse_args()

    result = download_sse_options_price(out_root=args.out_root)
    print(result)


if __name__ == "__main__":
    main()
