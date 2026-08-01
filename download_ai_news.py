"""
download_ai_news.py — Query Zhihu search API for industry news, references,
summary, and stock trading advice.

Workflow:
  1. Load ZHIHU_API_KEY from .env (project root).
  2. For each index-based industry in _classification.ICONIC_INDEXES, build a
     short prompt concatenated with the industry description, then call:
       https://developer.zhihu.com/api/v1/content/zhihu_search
  3. Parse the JSON response to extract related news, references, and a
     combined summary.
  4. Generate simple stock trading advice (rules-based) from the search
     results' sentiment signals.
  5. Save each industry's result as a markdown file under temps/ai_news/.

Usage:
    python download_ai_news.py                  # all indices
    python download_ai_news.py --index 930713   # single index code
    python download_ai_news.py --limit 5        # top-5 results per query
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests

from _download_commons import (
    setup_logger,
    resolve_out_dir,
    load_classification_indices,
    load_classification_index_names,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ZHIHU_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/zhihu_search"
ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_LIMIT = 10
DEFAULT_TIMEOUT: Tuple[int, int] = (15, 60)
SLEEP_SEC = 1.0  # polite delay between API calls

# Short prompt template prepended to the industry description. The Zhihu
# search API takes a single Query string, so we concatenate a short
# instruction with the industry name + index code to bias results toward
# recent news, investment analysis, and trading advice.
SHORT_PROMPT_TEMPLATE = (
    "{industry}行业 指数{code} 最新新闻 投资分析 股票交易建议 "
    "板块走势 龙头股"
)

logger = setup_logger("ai_news")


# ---------------------------------------------------------------------------
# .env loader (mirrors _db_commons._load_env_vars style; no python-dotenv)
# ---------------------------------------------------------------------------
def load_zhihu_api_key() -> str:
    """Read ZHIHU_API_KEY from environment or .env file at project root.

    Raises RuntimeError if the key is missing.
    """
    key = os.environ.get("ZHIHU_API_KEY")
    if key:
        return key.strip()

    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "ZHIHU_API_KEY":
                    return v.strip().strip("'\"")

    raise RuntimeError(
        f"ZHIHU_API_KEY not found. Set it in environment or in {ENV_FILE}"
    )


# ---------------------------------------------------------------------------
# Index → industry description lookup
# ---------------------------------------------------------------------------
def build_index_industry_map() -> List[Tuple[str, str, str, str]]:
    """Build a flat list of (index_code, index_name, sector_cn, industry_cn).

    Reads from sec_classification.json — the authoritative, hand-editable
    cache.  Indices classified as OTHER are skipped (the prompt is
    index-based by design, so unclassified indices are not useful).
    """
    import json as _json
    from pathlib import Path as _Path

    json_path = _Path(__file__).resolve().parent / "sec_classification.json"
    if not json_path.is_file():
        return []
    with json_path.open("r", encoding="utf-8") as f:
        state = _json.load(f)

    catalog = state.get("catalog", {})
    indices = state.get("indices", {})

    # Build (sector_id, industry_id) → (sector_cn, industry_cn) lookup.
    label_lookup: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for sid, sdata in catalog.items():
        s_cn = sdata.get("label", sid)
        for iid, idata in (sdata.get("industries") or {}).items():
            i_cn = idata.get("label", iid)
            label_lookup[(sid, iid)] = (s_cn, i_cn)

    rows: List[Tuple[str, str, str, str]] = []
    for code, info in indices.items():
        sid = info.get("sector_id", "OTHER")
        iid = info.get("industry_id", "OTHER")
        if sid == "OTHER" or iid == "OTHER":
            continue
        s_cn, i_cn = label_lookup.get((sid, iid), (sid, iid))
        rows.append((code, info.get("name", code), s_cn, i_cn))

    # Deduplicate by index_code (an index may appear under multiple
    # industries; keep the first occurrence).
    seen: set = set()
    unique: List[Tuple[str, str, str, str]] = []
    for row in rows:
        if row[0] in seen:
            continue
        seen.add(row[0])
        unique.append(row)
    # Stable sort by index code for reproducible output ordering
    unique.sort(key=lambda r: r[0])
    return unique


# ---------------------------------------------------------------------------
# Zhihu API client
# ---------------------------------------------------------------------------
def build_zhihu_headers(api_key: str) -> Dict[str, str]:
    """Build the required Authorization + X-Request-Timestamp headers."""
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }


def call_zhihu_search(
    session: requests.Session,
    api_key: str,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> Optional[Dict[str, Any]]:
    """Call the Zhihu content search API.

    Returns the parsed JSON dict on success, None on failure. Retries once
    with a fresh timestamp header on 401 (the server validates timestamp
    freshness).
    """
    params = {"Query": query, "limit": limit}
    headers = build_zhihu_headers(api_key)
    url = f"{ZHIHU_SEARCH_URL}?{urlencode(params)}"

    for attempt in range(2):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            logger.warning("Zhihu search request failed: %s", e)
            return None

        if resp.status_code == 401 and attempt == 0:
            logger.info("Retrying with fresh timestamp (401 on first attempt)")
            headers = build_zhihu_headers(api_key)
            time.sleep(SLEEP_SEC)
            continue

        if resp.status_code != 200:
            logger.error(
                "Zhihu search HTTP %d for query=%r body=%s",
                resp.status_code, query[:60], resp.text[:300],
            )
            return None

        try:
            return resp.json()
        except ValueError as e:
            logger.error("Zhihu search JSON parse failed: %s", e)
            return None

    return None


# ---------------------------------------------------------------------------
# Response parser — robust to multiple response shapes
# ---------------------------------------------------------------------------
@dataclass
class SearchResult:
    title: str = ""
    excerpt: str = ""
    url: str = ""
    author: str = ""
    voteup: int = 0
    type: str = ""
    published: str = ""


def _clean_highlight(s: str) -> str:
    """Strip Zhihu's <em>...</em> highlight tags from a string."""
    if not s:
        return ""
    return re.sub(r"</?em>", "", s).strip()


def _extract_result_items(payload: Any) -> List[Dict[str, Any]]:
    """Find the list of search-result items in a Zhihu response.

    Zhihu APIs historically return results under one of:
      - data (list)
      - data.data (list)
      - data.items (list)
      - data.search_result (list)
    This helper tries each in turn and returns the first non-empty list.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "search_result", "results", "list"):
            v = data.get(key)
            if isinstance(v, list) and v:
                return v
    for key in ("items", "search_result", "results", "list"):
        v = payload.get(key)
        if isinstance(v, list) and v:
            return v
    return []


def parse_search_results(payload: Dict[str, Any]) -> List[SearchResult]:
    """Convert raw Zhihu search JSON into a list of SearchResult."""
    raw_items = _extract_result_items(payload)
    results: List[SearchResult] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        obj = item.get("object") if isinstance(item.get("object"), dict) else item
        highlight = item.get("highlight") or {}
        title = (
            _clean_highlight(highlight.get("title"))
            or _clean_highlight(obj.get("title"))
            or item.get("title", "")
        )
        excerpt = (
            _clean_highlight(highlight.get("description"))
            or _clean_highlight(obj.get("excerpt"))
            or _clean_highlight(obj.get("summary"))
            or _clean_highlight(obj.get("content"))
            or item.get("excerpt", "")
        )
        # Truncate very long excerpts to keep the markdown readable
        if len(excerpt) > 400:
            excerpt = excerpt[:400].rstrip() + "..."
        url = (
            obj.get("url")
            or item.get("url")
            or obj.get("link")
            or ""
        )
        author_obj = obj.get("author") or {}
        if isinstance(author_obj, dict):
            author = author_obj.get("name", "")
        else:
            author = str(author_obj)
        voteup = int(obj.get("voteup_count") or obj.get("upvoted_count") or 0)
        type_str = obj.get("type") or item.get("type") or ""
        created = obj.get("created_time") or obj.get("published_time") or ""
        if isinstance(created, int):
            try:
                created = datetime.fromtimestamp(created).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                created = ""
        results.append(SearchResult(
            title=title, excerpt=excerpt, url=url,
            author=author, voteup=voteup,
            type=type_str, published=str(created),
        ))
    return results


# ---------------------------------------------------------------------------
# Summary + stock trading advice generator (rules-based, no LLM)
# ---------------------------------------------------------------------------
POSITIVE_KEYWORDS = [
    "上涨", "上涨", "增长", "突破", "新高", "利好", "回暖", "复苏",
    "放量", "强势", "超预期", "盈利", "回购", "增持", "牛市", "龙头",
    "景气", "需求旺盛", "供不应求",
]
NEGATIVE_KEYWORDS = [
    "下跌", "下跌", "下滑", "暴跌", "利空", "承压", "疲软", "亏损",
    "减仓", "减持", "熊市", "调整", "风险", "警示", "退市", "爆雷",
    "违约", "减产", "降价",
]


def _count_keywords(text: str, keywords: List[str]) -> int:
    return sum(text.count(k) for k in keywords)


def build_summary(results: List[SearchResult], industry: str) -> str:
    """Build a short combined summary from the top search results' excerpts."""
    if not results:
        return f"未检索到关于{industry}行业的最新内容。"
    excerpts = [r.excerpt for r in results[:5] if r.excerpt]
    if not excerpts:
        return f"检索到{len(results)}条与{industry}行业相关的内容，但摘要为空。"
    joined = " / ".join(excerpts)
    if len(joined) > 600:
        joined = joined[:600].rstrip() + "..."
    return f"基于{len(excerpts)}条知乎内容综合：{joined}"


def generate_trading_advice(
    results: List[SearchResult],
    industry: str,
    index_code: str,
) -> str:
    """Generate rules-based stock trading advice from search-result sentiment.

    Counts positive vs negative financial keywords across all result titles
    + excerpts, then emits a directional signal (看多 / 看空 / 中性) with a
    short rationale and the suggested focus tickers (extracted from titles
    when present).  This is NOT financial advice — it is a heuristic
    summary of community search signals.
    """
    if not results:
        return (
            f"暂无足够数据为{industry}（{index_code}）生成交易建议，"
            "建议关注官方财报与指数成分股公告。"
        )

    corpus = " ".join(
        (r.title or "") + " " + (r.excerpt or "") for r in results
    )
    pos = _count_keywords(corpus, POSITIVE_KEYWORDS)
    neg = _count_keywords(corpus, NEGATIVE_KEYWORDS)
    total = pos + neg
    top_results = results[:5]

    # Direction
    if total == 0:
        direction = "中性"
        rationale = "搜索结果未出现明显多空倾向关键词。"
    elif pos > neg * 1.5:
        direction = "看多"
        rationale = f"正面信号 {pos} 条，负面信号 {neg} 条，多头情绪占优。"
    elif neg > pos * 1.5:
        direction = "看空"
        rationale = f"负面信号 {neg} 条，正面信号 {pos} 条，空头情绪占优。"
    else:
        direction = "中性偏谨慎"
        rationale = f"正面 {pos} / 负面 {neg}，多空分歧较大，方向不明。"

    # Ticker extraction: pull any A-share-like code (6-digit) from titles
    ticker_codes: List[str] = []
    for r in top_results:
        for m in re.finditer(r"\b(60[013]\d{3}|688\d{3}|00[23]\d{3}|30[01]\d{3}|15\d{4}|51\d{4})\b", r.title or ""):
            code = m.group(1)
            if code not in ticker_codes:
                ticker_codes.append(code)

    lines = [
        f"### 交易建议：{direction}",
        f"- **方向**：{direction}",
        f"- **依据**：{rationale}",
        f"- **参考指数**：{index_code}（{industry}）",
    ]
    if ticker_codes:
        lines.append(f"- **搜索中提及的代码**：{', '.join(ticker_codes[:8])}")
    lines.append("")
    lines.append("> 本建议由搜索结果关键词启发式生成，仅供参考，不构成投资建议。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------
@dataclass
class IndustryNewsReport:
    index_code: str
    index_name: str
    sector_cn: str
    industry_cn: str
    query: str
    results: List[SearchResult] = field(default_factory=list)
    summary: str = ""
    advice: str = ""
    fetched_at: str = ""

    def md_filename(self) -> str:
        return f"ai_news_{self.index_code}_{self.fetched_at or 'na'}.md"

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append("---")
        lines.append(f"index_code: {self.index_code}")
        lines.append(f"index_name: {self.index_name}")
        lines.append(f"sector: {self.sector_cn}")
        lines.append(f"industry: {self.industry_cn}")
        lines.append(f"fetched_at: {self.fetched_at}")
        lines.append(f"result_count: {len(self.results)}")
        lines.append(f"query: {self.query}")
        lines.append("---")
        lines.append("")
        lines.append(
            f"# AI 行业资讯 — {self.sector_cn} / {self.industry_cn} "
            f"（指数 {self.index_code} {self.index_name}）"
        )
        lines.append("")
        lines.append(f"- 抓取时间：**{self.fetched_at}**")
        lines.append(f"- 板块：{self.sector_cn}")
        lines.append(f"- 行业：{self.industry_cn}")
        lines.append(f"- 跟踪指数：{self.index_code} {self.index_name}")
        lines.append(f"- 搜索 Query：`{self.query}`")
        lines.append("")

        # Summary
        lines.append("## 摘要 Summary")
        lines.append("")
        lines.append(self.summary)
        lines.append("")

        # News + references
        lines.append("## 相关新闻 Related News")
        lines.append("")
        if self.results:
            for i, r in enumerate(self.results, 1):
                lines.append(f"### {i}. {r.title or '(无标题)'}")
                lines.append("")
                meta_bits: List[str] = []
                if r.author:
                    meta_bits.append(f"作者：{r.author}")
                if r.type:
                    meta_bits.append(f"类型：{r.type}")
                if r.published:
                    meta_bits.append(f"发布：{r.published}")
                if r.voteup:
                    meta_bits.append(f"赞同：{r.voteup}")
                if meta_bits:
                    lines.append("- " + " | ".join(meta_bits))
                if r.excerpt:
                    lines.append(f"- 摘要：{r.excerpt}")
                if r.url:
                    lines.append(f"- 链接：{r.url}")
                lines.append("")
        else:
            lines.append("(未检索到相关新闻)")
            lines.append("")

        # References (compact URL list)
        lines.append("## 参考资料 References")
        lines.append("")
        if self.results:
            for i, r in enumerate(self.results, 1):
                label = r.title or r.url or f"#{i}"
                if r.url:
                    lines.append(f"{i}. [{label}]({r.url})")
                else:
                    lines.append(f"{i}. {label}（无链接）")
        else:
            lines.append("(无)")
        lines.append("")

        # Trading advice
        lines.append("## 股票交易建议 Stock Trading Advice")
        lines.append("")
        lines.append(self.advice)
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def fetch_industry_news(
    session: requests.Session,
    api_key: str,
    index_code: str,
    index_name: str,
    sector_cn: str,
    industry_cn: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> IndustryNewsReport:
    query = SHORT_PROMPT_TEMPLATE.format(
        industry=index_name or industry_cn,
        code=index_code,
    )
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = IndustryNewsReport(
        index_code=index_code,
        index_name=index_name,
        sector_cn=sector_cn,
        industry_cn=industry_cn,
        query=query,
        fetched_at=fetched_at,
    )

    payload = call_zhihu_search(session, api_key, query, limit=limit)
    if payload is None:
        logger.warning("[%s %s] API returned no payload", index_code, index_name)
        report.summary = f"调用知乎搜索 API 失败，未获取到 {index_name} 相关内容。"
        report.advice = generate_trading_advice([], index_name, index_code)
        return report

    results = parse_search_results(payload)
    report.results = results
    report.summary = build_summary(results, index_name or industry_cn)
    report.advice = generate_trading_advice(results, index_name, index_code)
    logger.info(
        "[%s %s] query=%r -> %d results",
        index_code, index_name, query[:50], len(results),
    )
    return report


def download_ai_news(
    *,
    out_root: Optional[str] = None,
    index_filter: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    sleep_sec: float = SLEEP_SEC,
) -> Dict[str, Any]:
    """Download AI news for all (or one) index-based industry.

    Args:
        out_root: Override output directory root.
        index_filter: If set, only fetch the industry whose index_code matches.
        limit: Max number of Zhihu search results per query.
        sleep_sec: Polite delay between API calls.
    """
    api_key = load_zhihu_api_key()
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "ai_news", out_root)

    industry_rows = build_index_industry_map()
    if index_filter:
        industry_rows = [r for r in industry_rows if r[0] == index_filter]
        if not industry_rows:
            raise ValueError(
                f"Index code {index_filter!r} not found in sec_classification.json. "
                f"Valid examples: {sorted(load_classification_index_names().keys())[:10]} ..."
            )

    logger.info(
        "Starting AI news download: %d industries, limit=%d, out=%s",
        len(industry_rows), limit, out_dir,
    )

    session = requests.Session()
    saved_files: List[str] = []
    failed: int = 0

    try:
        for i, (code, name, sector, industry) in enumerate(industry_rows, 1):
            logger.info("[%d/%d] %s %s (%s/%s)", i, len(industry_rows),
                        code, name, sector, industry)
            report = fetch_industry_news(
                session, api_key, code, name, sector, industry, limit=limit,
            )
            fpath = out_dir / report.md_filename()
            fpath.write_text(report.to_markdown(), encoding="utf-8")
            saved_files.append(str(fpath))
            if not report.results:
                failed += 1
            if sleep_sec > 0 and i < len(industry_rows):
                time.sleep(sleep_sec)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = {
        "total": len(industry_rows),
        "saved": len(saved_files),
        "failed": failed,
        "out_dir": str(out_dir),
        "files": saved_files,
    }
    logger.info(
        "Done AI news. total=%d saved=%d failed=%d out=%s",
        summary["total"], summary["saved"], failed, out_dir,
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download AI news from Zhihu search API for index-based industries.",
    )
    parser.add_argument(
        "--index", type=str, default=None,
        help="Only fetch a single index code (e.g. 930713 for 中证人工智能).",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"Max Zhihu search results per query (default {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--sleep", type=float, default=SLEEP_SEC,
        help=f"Delay between API calls in seconds (default {SLEEP_SEC}).",
    )
    parser.add_argument(
        "--out-root", type=str, default=None,
        help="Override output directory root (default: <script>/temps/ai_news/).",
    )
    parser.add_argument(
        "--list-indices", action="store_true",
        help="Print all available index codes and exit.",
    )
    args = parser.parse_args()

    if args.list_indices:
        rows = build_index_industry_map()
        print(f"{'index_code':<10} {'index_name':<20} sector  industry")
        for code, name, sector, industry in rows:
            print(f"{code:<10} {name:<20} {sector:<8} {industry}")
        print(f"\nTotal: {len(rows)} index-based industries")
    else:
        result = download_ai_news(
            out_root=args.out_root,
            index_filter=args.index,
            limit=args.limit,
            sleep_sec=args.sleep,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
