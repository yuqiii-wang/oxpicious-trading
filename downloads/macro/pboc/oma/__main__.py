"""download_pboc_oma.py — Download and parse PBoC Open Market Announcements
(公开市场业务公告) from www.pbc.gov.cn.

Source list page:
    https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125469/index.html

These are HIGH-LEVEL policy announcements (about 89 records from 2020-2026,
5 list pages) — distinct from the daily transaction announcements scraped by
download_pboc_repo_news.py. Examples:

  · 隔夜逆回购 (overnight reverse repo) tool introduction / scheduling
  · 买断式逆回购 (outright reverse repo) tool introduction
  · 公开市场业务一级交易商 (primary dealer) annual list
  · 央行票据 (central bank bills) new issuances
  · 中期借贷便利 (MLF) tool policy changes

Each announcement body is parsed into a markdown file with YAML front-matter
(title / pub_date / detail_url / type / keywords / serial_year / serial_no),
then converted to a per-file CSV and aggregated into:

    temps/pboc_oma_news/oma_combined.csv    (all rows)
    temps/pboc_oma_news/oma_keywords.csv    (keyword matches)

The keywords CSV is a long-format summary — one row per (date, title, keyword)
match — so downstream tools can quickly answer "which announcements mentioned
基点 / 隔夜逆回购 / etc on what dates".

Output layout mirrors download_pboc_lpr_news.py:
    temps/pboc_oma_news/pboc_oma_<date>_<slug>.md
    temps/pboc_oma_news/pboc_oma_<date>_<slug>.csv

Usage:
    python download_pboc_oma.py
    python download_pboc_oma.py --start-date 2024-01-01
    python download_pboc_oma.py --reparse   # re-parse existing .md files
"""
from __future__ import annotations


import argparse
import csv as _csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from downloads._common import (
    COMMON_BASE_HEADERS,
    DEFAULT_TIMEOUT,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    resolve_out_dir,
    parse_date_window,
    scan_present_dates_with_pattern,
    build_default_session,
    RunStats,
)
# Reuse the well-tested date-parsing utilities from the repo-news downloader
from downloads.macro.pboc._common.parsing import (
    RE_CHINESE_DATE,
    RE_TITLE_DATE,
    RE_PUBDATE_META,
    RE_DETAIL_SLUG,
    RE_SERIAL,
    parse_chinese_date,
    _clean_text,
)


PBOC_BASE = "https://www.pbc.gov.cn"

# PBoC Open Market Announcements list page (货币政策司 → 公开市场业务 → 业务公告)
OMA_LIST_BASE = "/zhengcehuobisi/125207/125213/125431/125469/"
OMA_FILE_PREFIX = "pboc_oma"
OMA_CATEGORY = "oma"

PBOC_MIN_VALID_BYTES = 200
SLEEP_SEC = 5.0
EMPTY_PLACEHOLDER_SUFFIX = "_empty.md"

# Paging tag detection — PBoC list pages use a JS-generated paging URL like
# `<a href=".../index_2.html">` or a templated slug. Same regex as repo news.
RE_PAGING_TAG = re.compile(r"""tagname=(['"])([^'"]*/(\w+)-(\d+)\.html)\1""")

# Date embedded in URL for newer announcements: /2026072413564746531/index.html
RE_URL_DATE = re.compile(r"/(\d{8})\d*/index\.html$")

# Date appearing immediately after the </a> in the list page (no <span>):
# `<a href="...">title</a>2026-07-24`
RE_TRAILING_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ============================================================================
# Keyword catalogue — each entry maps a canonical "type" to the keywords that
# classify an announcement as that type. The first matching type wins (in
# declaration order), so order matters: most-specific first.
# ============================================================================
# Each tuple: (type_slug, [regex_patterns...], [display_keywords...])
# Patterns are matched against the body+title text (case-insensitive, Unicode).
KEYWORD_CATALOGUE: List[Tuple[str, List[str], List[str]]] = [
    # Tool introductions / scheduling
    ("overnight_reverse_repo",
     [r"隔夜逆回购"],
     ["隔夜逆回购"]),
    ("outright_repo",
     [r"买断式逆回购"],
     ["买断式逆回购"]),
    ("central_bank_bill",
     [r"央行票据", r"中央银行票据"],
     ["央行票据"]),
    ("mlf",
     [r"中期借贷便利", r"\bMLF\b"],
     ["中期借贷便利", "MLF"]),
    ("primary_dealer",
     [r"一级交易商"],
     ["一级交易商"]),
    # Rate-change keywords (basis-point adjustments)
    ("interest_rate",
     [r"\d+\s*个?\s*基点", r"基点", r"操作利率", r"中标利率", r"LPR", r"贷款市场报价利率"],
     ["基点", "操作利率", "中标利率", "LPR"]),
    # Generic tool introductions
    ("tool_introduction",
     [r"启用.*操作工具", r"新增.*操作工具", r"丰富.*货币政策工具箱"],
     ["启用", "操作工具"]),
]

# Flat list of all display keywords (for the keyword-match CSV)
ALL_KEYWORDS: List[str] = sorted({kw for _, _, kws in KEYWORD_CATALOGUE for kw in kws})


def _ws(cn: str) -> str:
    """Build a whitespace-tolerant regex for a Chinese phrase."""
    return r"\s*".join(re.escape(ch) for ch in cn)


# Pre-compile keyword patterns (whitespace-tolerant)
_COMPILED_KEYWORD_PATTERNS: List[Tuple[str, List[re.Pattern], List[str]]] = [
    (slug, [re.compile(_ws(p), re.IGNORECASE) for p in pats], kws)
    for slug, pats, kws in KEYWORD_CATALOGUE
]


def classify_announcement(text: str) -> Tuple[str, List[str]]:
    """Classify an announcement body+title and return (type_slug, matched_keywords).

    The first matching type in KEYWORD_CATALOGUE wins. Matched keywords from
    ALL types are collected (so an announcement can be `overnight_reverse_repo`
    type but also flag `基点` if it mentions a rate change).
    """
    matched: List[str] = []
    chosen_type = "other"
    for slug, patterns, kws in _COMPILED_KEYWORD_PATTERNS:
        type_matched = False
        for pat in patterns:
            if pat.search(text):
                type_matched = True
                break
        if type_matched:
            for kw in kws:
                if kw not in matched:
                    matched.append(kw)
            if chosen_type == "other":
                chosen_type = slug
    return chosen_type, matched


logger = setup_logger("pboc_oma_news")


# ============================================================================
# Data class
# ============================================================================
@dataclass
class OmaAnnouncementItem:
    title: str
    detail_url: str
    list_page: int = 0
    pub_date: Optional[str] = None
    detail_slug: Optional[str] = None
    serial_year: Optional[str] = None
    serial_no: Optional[str] = None
    type: str = "other"
    keywords: List[str] = field(default_factory=list)
    raw_body: str = ""
    parse_warnings: List[str] = field(default_factory=list)

    def md_filename(self) -> str:
        d = self.pub_date or "00000000"
        slug = self.detail_slug or "x"
        return f"{OMA_FILE_PREFIX}_{d}_{slug}.md"

    def to_markdown(self) -> str:
        lines = [
            "---",
            f"category: {OMA_CATEGORY}",
            f"title: {self.title}",
            f"detail_url: {self.detail_url}",
            f"pub_date: {self.pub_date or ''}",
            f"type: {self.type}",
            f"keywords: {'|'.join(self.keywords)}",
        ]
        if self.serial_year:
            lines.append(f"serial_year: {self.serial_year}")
        if self.serial_no:
            lines.append(f"serial_no: {self.serial_no}")
        if self.parse_warnings:
            lines.append(f"parse_warnings: {self.parse_warnings!r}")
        lines += [
            "---",
            "",
            f"# {self.title}",
            "",
            f"- Pub date: **{self.pub_date or 'n/a'}**",
            f"- Type: **{self.type}**",
            f"- Keywords: {', '.join(self.keywords) if self.keywords else '(none)'}",
            f"- Detail: {self.detail_url}",
            "",
            "## Raw body",
            "",
            "```",
            self.raw_body.strip(),
            "```",
            "",
        ]
        return "\n".join(lines)


# ============================================================================
# CSV columns
# ============================================================================
PBOC_OMA_CSV_COLUMNS = [
    "pub_date",
    "title",
    "type",
    "keywords",
    "detail_url",
    "serial_year",
    "serial_no",
    "detail_slug",
    "content",
    "source_file",
]

PBOC_OMA_KEYWORDS_CSV_COLUMNS = [
    "pub_date",
    "title",
    "keyword",
    "type",
]


# ============================================================================
# Date estimation from title / URL
# ============================================================================
def estimate_item_date(item: OmaAnnouncementItem) -> Optional[date]:
    if item.pub_date:
        try:
            return datetime.strptime(item.pub_date, "%Y-%m-%d").date()
        except ValueError:
            pass
    m = RE_TITLE_DATE.search(item.title)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = RE_CHINESE_DATE.search(item.title)
    if m:
        return parse_chinese_date(m.group(1))
    return None


def create_empty_placeholder(out_dir: Path, d: date) -> None:
    fname = f"{OMA_FILE_PREFIX}_{d.strftime('%Y-%m-%d')}_empty.md"
    fpath = out_dir / fname
    if not fpath.exists():
        content = (
            f"---\ncategory: {OMA_CATEGORY}\n"
            f"pub_date: {d.strftime('%Y-%m-%d')}\nstatus: empty\n---\n"
            f"# No OMA announcement for {d.strftime('%Y-%m-%d')}\n\n"
            f"No PBoC Open Market Announcement found for this date.\n"
        )
        fpath.write_text(content, encoding="utf-8")


# ============================================================================
# Body parsing
# ============================================================================
def parse_oma_body(item: OmaAnnouncementItem, html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    meta_area = soup.get_text(" ", strip=False)

    # --- pub_date --------------------------------------------------------
    pd: Optional[date] = None
    m = RE_PUBDATE_META.search(meta_area)
    if m:
        try:
            pd = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pd = None
    if pd is None:
        mc = RE_CHINESE_DATE.search(meta_area)
        if mc:
            pd = parse_chinese_date(mc.group(1))
    if pd is None:
        m2 = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", meta_area)
        if m2:
            try:
                pd = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            except ValueError:
                pd = None
    if pd is None:
        item.parse_warnings.append("pub_date not found")
    else:
        item.pub_date = pd.strftime("%Y-%m-%d")

    # --- content body ----------------------------------------------------
    content_div = (
        soup.find("div", id="zoom")
        or soup.find("div", class_="content")
        or soup.find("td", class_="Normal")
    )
    if content_div is None:
        candidates = soup.find_all(
            ["div", "td"], class_=re.compile(r"(TRS_Editor|content|Normal)", re.I)
        )
        for c in candidates:
            txt = c.get_text("\n", strip=False)
            if len(txt) > 30 and any(
                k in txt for k in ("公开市场", "逆回购", "MLF", "一级交易商", "操作", "公告")
            ):
                content_div = c
                break
    if content_div is None:
        content_div = soup.body or soup

    body_text = _clean_text(content_div.get_text("\n", strip=False))
    item.raw_body = body_text

    # --- serial number (e.g. [2026]第5号) --------------------------------
    ms = RE_SERIAL.search(item.title)
    if not ms:
        ms = RE_SERIAL.search(body_text)
    if ms:
        item.serial_year = ms.group(1)
        item.serial_no = ms.group(2)
    else:
        item.parse_warnings.append("serial number not found")

    # --- classify type + extract keywords --------------------------------
    # Search both title and body so e.g. "隔夜逆回购" in title is captured
    # even if the body uses different phrasing.
    classification_text = f"{item.title}\n{body_text}"
    item.type, item.keywords = classify_announcement(classification_text)
    if not item.keywords:
        item.parse_warnings.append("no keywords matched")

    # --- detail slug -----------------------------------------------------
    mslug = RE_DETAIL_SLUG.search(item.detail_url)
    if mslug:
        item.detail_slug = mslug.group(1)


# ============================================================================
# List-page fetching & pagination (adapted from LPR / repo news)
# ============================================================================
def build_session() -> requests.Session:
    s = build_default_session()
    s.headers.update(COMMON_BASE_HEADERS)
    return s


def detect_page_prefix(html: str) -> Optional[str]:
    matches = list(RE_PAGING_TAG.finditer(html))
    if not matches:
        return None
    by_page: Dict[int, str] = {}
    for m in matches:
        slug = m.group(3)
        page_no = int(m.group(4))
        by_page[page_no] = f"{slug}-{{page}}.html"
    if not by_page:
        return None
    return by_page[max(by_page.keys())]


def list_page_url(page: int, page_prefix_fmt: Optional[str] = None) -> str:
    if page <= 1:
        return PBOC_BASE + OMA_LIST_BASE + "index.html"
    if page_prefix_fmt:
        return PBOC_BASE + OMA_LIST_BASE + page_prefix_fmt.format(page=page)
    return PBOC_BASE + OMA_LIST_BASE + f"index_{page}.html"


def _looks_like_oma_link(text: str) -> bool:
    """Filter list-page <a> links: keep only OMA announcements.

    OMA titles look like "中国人民银行公开市场业务公告［2026］第5号" or
    "公开市场业务公告 ［2024］第5号" — they contain "公开市场业务公告".
    """
    if not text:
        return False
    if "公开市场业务公告" in text:
        return True
    # Fallback: title contains "公告" + a year-bracket pattern like ［2024］
    if "公告" in text and re.search(r"［\d{4}］|\[\d{4}\]", text):
        return True
    return False


def _extract_date_from_sibling(a_tag) -> Optional[str]:
    """Try to extract a YYYY-MM-DD date from the text immediately following
    an <a> tag on the list page.

    PBoC list pages render dates either in a <span> sibling or as bare text
    after the closing </a>.
    """
    # 1. <span> sibling
    next_span = a_tag.find_next("span")
    if next_span:
        span_text = next_span.get_text(strip=True)
        m = RE_TRAILING_DATE.match(span_text)
        if m:
            return m.group(1)

    # 2. Bare text in next sibling (NavigableString)
    nxt = a_tag.next_sibling
    while nxt is not None:
        if hasattr(nxt, "get_text"):
            # Skipped an intermediate tag (e.g. <br>) — stop to avoid pulling
            # dates from unrelated entries.
            break
        text = str(nxt).strip()
        if text:
            m = RE_TRAILING_DATE.match(text)
            if m:
                return m.group(1)
            # If we hit non-empty text that isn't a date, stop.
            break
        nxt = nxt.next_sibling
    return None


def fetch_list_page(
    session: requests.Session,
    page: int,
    page_prefix_fmt: Optional[str] = None,
    proxy: Optional[AntiBotProxy] = None,
) -> Tuple[List[OmaAnnouncementItem], Optional[str]]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))

    url = list_page_url(page, page_prefix_fmt)
    logger.info("Fetching OMA list page %d (%s)", page, url)

    resp = proxy.get(
        session,
        url,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[list oma p{page}]",
    )
    if resp is None:
        logger.error("List page fetch failed page=%d: request returned None", page)
        return [], None

    resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text
    detected_prefix = None
    if page == 1:
        detected_prefix = detect_page_prefix(html)
        if detected_prefix:
            logger.info("  Detected pagination format: %s", detected_prefix)

    # The list page URL itself (e.g. .../125469/index.html) matches
    # RE_DETAIL_SLUG because the directory "125469" is 7 digits. Exclude it
    # so we don't fetch the list page as an announcement.
    list_page_full_url = PBOC_BASE + OMA_LIST_BASE + "index.html"

    soup = BeautifulSoup(html, "html.parser")
    items: List[OmaAnnouncementItem] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href:
            continue
        if href.startswith("./"):
            href = href[2:]
        if not href.startswith("http"):
            if not href.startswith("/"):
                href = OMA_LIST_BASE + href
            href = PBOC_BASE + href
        text = a.get_text(strip=True).strip('"“”').strip()
        if not text:
            continue
        # Skip obvious navigation / boilerplate
        if text in {"公告信息", "中国人民银行", "货币政策司", "首页", "上一页", "下一页", "尾页"}:
            continue
        if href == list_page_full_url or href.rstrip("/").endswith(OMA_LIST_BASE.rstrip("/")):
            continue
        if not RE_DETAIL_SLUG.search(href):
            continue
        if not _looks_like_oma_link(text):
            continue

        pub_date = _extract_date_from_sibling(a)
        if pub_date is None:
            # Try URL-embedded date for newer announcements
            m = RE_URL_DATE.search(href)
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%Y%m%d").date()
                    pub_date = d.strftime("%Y-%m-%d")
                except ValueError:
                    pass

        items.append(
            OmaAnnouncementItem(
                title=text,
                detail_url=href,
                list_page=page,
                pub_date=pub_date,
            )
        )

    # Dedup by detail_url (links can appear twice on the page)
    dedup: Dict[str, OmaAnnouncementItem] = {}
    for it in items:
        dedup[it.detail_url] = it
    return list(dedup.values()), detected_prefix


def smart_pagination_pages(
    session: requests.Session,
    target_start: date,
    max_pages: int = 20,
    jump_interval: int = 2,
    proxy: Optional[AntiBotProxy] = None,
) -> Tuple[List[int], Optional[str]]:
    """Walk OMA list pages until the oldest entry on a page predates target_start.

    OMA announcements are infrequent (~89 records, 5 pages from 2020-2026).
    We use a small jump+incremental search.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))

    page_prefix_fmt: Optional[str] = None
    items, detected_fmt = fetch_list_page(session, 1, page_prefix_fmt, proxy)
    if detected_fmt:
        page_prefix_fmt = detected_fmt
    if not items:
        logger.info("[smart-pagination] page 1 returned no items")
        return [], page_prefix_fmt

    page_dates_1 = [d for d in (estimate_item_date(it) for it in items) if d is not None]
    if not page_dates_1:
        logger.warning("[smart-pagination] no dates found on page 1")
        return [1], page_prefix_fmt

    oldest_on_page_1 = min(page_dates_1)
    logger.info(
        "[smart-pagination] page 1: oldest=%s, target=%s",
        oldest_on_page_1, target_start,
    )
    if oldest_on_page_1 < target_start:
        logger.info("[smart-pagination] page 1 already spans target boundary")
        return [1], page_prefix_fmt

    current_page = 1
    last_in_range_page = 1
    while current_page < max_pages:
        if proxy.is_blocked(PBOC_BASE):
            logger.warning("[smart-pagination] host blocked, stopping")
            break

        next_jump = min(current_page + jump_interval, max_pages)
        logger.info(
            "[smart-pagination] jumping from page %d to page %d",
            current_page, next_jump,
        )
        jump_items, _ = fetch_list_page(session, next_jump, page_prefix_fmt, proxy)
        jump_dates = [d for d in (estimate_item_date(it) for it in jump_items) if d is not None]

        if not jump_items:
            logger.info("[smart-pagination] page %d empty, boundary at page %d",
                        next_jump, last_in_range_page)
            break
        if not jump_dates:
            current_page = next_jump
            continue

        if max(jump_dates) >= target_start:
            last_in_range_page = next_jump
            current_page = next_jump
            continue

        for p in range(last_in_range_page + 1, next_jump + 1):
            inc_items, _ = fetch_list_page(session, p, page_prefix_fmt, proxy)
            if not inc_items:
                break
            inc_dates = [d for d in (estimate_item_date(it) for it in inc_items) if d is not None]
            if not inc_dates:
                continue
            if max(inc_dates) >= target_start:
                last_in_range_page = p
            else:
                break
        break

    return list(range(1, last_in_range_page + 1)), page_prefix_fmt


def fetch_detail(
    session: requests.Session, item: OmaAnnouncementItem,
    proxy: Optional[AntiBotProxy] = None,
) -> bool:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    resp = proxy.get(
        session,
        item.detail_url,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[detail {item.title[:30]}]",
    )
    if resp is None:
        logger.error("Detail fetch failed %s: None", item.title[:40])
        return False
    resp.encoding = resp.apparent_encoding or "utf-8"
    if len(resp.content) < PBOC_MIN_VALID_BYTES:
        logger.warning("Detail too small (%d bytes) for %s",
                       len(resp.content), item.title[:40])
        return False
    parse_oma_body(item, resp.text)
    return True


# ============================================================================
# CSV conversion
# ============================================================================
def _parse_oma_fm_simple(text: str) -> Dict[str, str]:
    """Parse YAML front-matter of an OMA .md file into a flat dict (stdlib)."""
    fm: Dict[str, str] = {}
    if not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end < 0:
        return fm
    block = text[3:end].strip()
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip().strip("'\"")
    return fm


def convert_md_to_csv(md_path: Path, csv_path: Optional[Path] = None) -> bool:
    """Parse a single OMA .md file → per-file CSV. Returns True if a row was written."""
    if csv_path is None:
        csv_path = md_path.with_suffix(".csv")
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[conv %s] cannot read: %s", md_path.name, e)
        return False

    fm = _parse_oma_fm_simple(text)
    pub_date = fm.get("pub_date", "")
    if not pub_date or fm.get("status") == "empty":
        return False

    # Extract raw body from ``` ... ``` block to use as content
    body_section = text[text.find("\n---", 3) + 4:]
    raw_start = body_section.find("```")
    raw_end = body_section.find("```", raw_start + 3) if raw_start >= 0 else -1
    if raw_start >= 0 and raw_end > raw_start:
        content = body_section[raw_start + 3:raw_end].strip()
    else:
        content = ""

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=PBOC_OMA_CSV_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "pub_date":       pub_date,
            "title":          fm.get("title", ""),
            "type":           fm.get("type", "other"),
            "keywords":       fm.get("keywords", ""),
            "detail_url":     fm.get("detail_url", ""),
            "serial_year":    fm.get("serial_year", ""),
            "serial_no":      fm.get("serial_no", ""),
            "detail_slug":    fm.get("detail_slug", ""),
            "content":        content,
            "source_file":    md_path.name,
        })
    return True


def build_oma_combined_csv(md_dir: Path, output_dir: Optional[Path] = None) -> Dict[str, int]:
    """Aggregate all pboc_oma_*.md (non-empty) files into oma_combined.csv.

    Prefers reading existing per-file CSVs (fast); falls back to parsing .md
    files when a per-file CSV is missing.

    Also writes oma_keywords.csv (one row per (date, title, keyword) match).
    """
    if output_dir is None:
        output_dir = md_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    combined_path = output_dir / "oma_combined.csv"
    keywords_path = output_dir / "oma_keywords.csv"

    md_files = sorted(md_dir.glob(f"{OMA_FILE_PREFIX}_*.md"))
    md_files = [f for f in md_files if not f.name.endswith(EMPTY_PLACEHOLDER_SUFFIX)]
    csv_files = sorted(md_dir.glob(f"{OMA_FILE_PREFIX}_*.csv"))

    logger.info("[build-csv] scanning %s: %d .md files, %d per-file CSVs",
                md_dir, len(md_files), len(csv_files))

    counts = {"rows": 0, "files_ok": 0, "files_empty": 0, "files_failed": 0,
              "keyword_rows": 0}

    # Collect all rows in memory so we can write both combined + keywords CSVs
    all_rows: List[Dict[str, str]] = []

    md_stems = {f.stem: f for f in md_files}
    csv_stems = {f.stem: f for f in csv_files}
    processed_stems = set()

    # 1. Read existing per-file CSVs
    for stem, csv_path in csv_stems.items():
        if stem.endswith("_empty"):
            continue
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = _csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            counts["files_failed"] += 1
            logger.warning("[build-csv] failed to read %s: %s", csv_path.name, e)
            continue
        if not rows:
            counts["files_empty"] += 1
            continue
        for row in rows:
            all_rows.append({col: row.get(col, "") for col in PBOC_OMA_CSV_COLUMNS})
            counts["rows"] += 1
        counts["files_ok"] += 1
        processed_stems.add(stem)

    # 2. For .md files without a per-file CSV, parse on-the-fly
    for stem, md_path in md_stems.items():
        if stem in processed_stems or stem.endswith("_empty"):
            continue
        per_file_csv = md_path.with_suffix(".csv")
        try:
            ok = convert_md_to_csv(md_path, per_file_csv)
        except Exception as e:
            counts["files_failed"] += 1
            logger.warning("[build-csv] failed to parse %s: %s", md_path.name, e)
            continue
        if not ok:
            counts["files_empty"] += 1
            continue
        try:
            with open(per_file_csv, "r", encoding="utf-8-sig", newline="") as f:
                reader = _csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            counts["files_failed"] += 1
            logger.warning("[build-csv] failed to re-read %s: %s", per_file_csv.name, e)
            continue
        for row in rows:
            all_rows.append({col: row.get(col, "") for col in PBOC_OMA_CSV_COLUMNS})
            counts["rows"] += 1
        counts["files_ok"] += 1

    # Sort by pub_date then title for stable output
    all_rows.sort(key=lambda r: (r.get("pub_date", ""), r.get("title", "")))

    # Write combined CSV
    with open(combined_path, "w", encoding="utf-8-sig", newline="") as fout:
        writer = _csv.DictWriter(fout, fieldnames=PBOC_OMA_CSV_COLUMNS)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    # Write keywords CSV (long format — one row per (date, title, keyword))
    with open(keywords_path, "w", encoding="utf-8-sig", newline="") as fout:
        writer = _csv.DictWriter(fout, fieldnames=PBOC_OMA_KEYWORDS_CSV_COLUMNS)
        writer.writeheader()
        for row in all_rows:
            kws_str = row.get("keywords", "")
            if not kws_str:
                continue
            for kw in kws_str.split("|"):
                kw = kw.strip()
                if not kw:
                    continue
                writer.writerow({
                    "pub_date":  row.get("pub_date", ""),
                    "title":     row.get("title", ""),
                    "keyword":   kw,
                    "type":      row.get("type", "other"),
                })
                counts["keyword_rows"] += 1

    logger.info("[build-csv] saved %s (%d rows, %d ok, %d empty, %d failed)",
                combined_path, counts["rows"], counts["files_ok"],
                counts["files_empty"], counts["files_failed"])
    logger.info("[build-csv] saved %s (%d keyword-match rows)",
                keywords_path, counts["keyword_rows"])
    return counts


# ============================================================================
# Main download entry point
# ============================================================================
def download_pboc_oma_news(
    *,
    out_root: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    years: Optional[int] = None,
    max_pages: int = 10,
    sleep_sec: float = SLEEP_SEC,
    convert_csv: bool = True,
    build_csv: bool = True,
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "pboc_oma_news", out_root)

    # OMA announcements go back to 2020; default to that start
    if start_date is None and years is not None:
        _start, _end = parse_date_window(
            end_date=end_date, start_date=None, lookback_years=years,
        )
    else:
        if start_date is None:
            start_date = "2020-01-01"
        _start, _end = parse_date_window(
            end_date=end_date, start_date=start_date, lookback_years=None,
        )

    session = build_session()
    stats = RunStats()
    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
        enable_host_tracking=False,
    )
    proxy = AntiBotProxy(proxy_config)

    cached_dates = scan_present_dates_with_pattern(
        out_dir, prefixes=[OMA_FILE_PREFIX], min_bytes=100, ext_glob="*.md",
    ).get(OMA_FILE_PREFIX, set())
    stats.skipped_cached = len(cached_dates)
    if cached_dates:
        logger.info("[oma] %d dates already cached, latest=%s",
                    len(cached_dates), max(cached_dates))
    else:
        logger.info("[oma] no prior cached files")

    # Build a set of existing file stems (slug-based) so announcements that
    # share a pub_date (e.g. [2026]第2号 and [2026]第3号 both on 2026-06-17)
    # are tracked independently — `cached_dates` alone would skip the second
    # one as a false cache hit.
    existing_file_stems: set = {f.stem for f in out_dir.glob(f"{OMA_FILE_PREFIX}_*.md")}

    logger.info("Starting PBoC OMA download: %s -> %s", _start, _end)

    skipped_oob = 0
    try:
        target_start = _start
        pages_to_process, page_prefix_fmt = smart_pagination_pages(
            session, target_start, max_pages=max_pages, jump_interval=1, proxy=proxy,
        )
        if not pages_to_process:
            logger.info("  no pages to process")
            return {}

        found_dates = set()
        reached_boundary = False

        for page in pages_to_process:
            if proxy.is_blocked(PBOC_BASE):
                logger.warning("  [host-blocked] stopping")
                break

            items, detected = fetch_list_page(session, page, page_prefix_fmt, proxy)
            if detected and not page_prefix_fmt:
                page_prefix_fmt = detected
            if not items:
                logger.info("  page %d returned no items, stopping", page)
                break
            logger.info("  page %d: %d candidate items", page, len(items))

            page_in_range_count = 0
            for item in items:
                if proxy.is_blocked(PBOC_BASE):
                    logger.warning("  [host-blocked] skipping remaining items")
                    reached_boundary = True
                    break

                mslug = RE_DETAIL_SLUG.search(item.detail_url)
                if mslug:
                    item.detail_slug = mslug.group(1)

                ok = fetch_detail(session, item, proxy)
                if not ok:
                    stats.failed += 1
                    # Auto-sleep handled by proxy.get()/post()
                    continue

                if not item.pub_date:
                    stats.failed += 1
                    # Auto-sleep handled by proxy.get()/post()
                    continue

                try:
                    d = datetime.strptime(item.pub_date, "%Y-%m-%d").date()
                except ValueError:
                    stats.failed += 1
                    # Auto-sleep handled by proxy.get()/post()
                    continue

                found_dates.add(d)

                fname = item.md_filename()
                fpath = out_dir / fname
                if fname[:-3] in existing_file_stems:
                    stats.skipped_cached += 1
                    page_in_range_count += 1
                    proxy.sleep(max(0.1, sleep_sec * 0.3))
                    continue

                if d < _start:
                    skipped_oob += 1
                    logger.info("  [boundary %s < %s] stop: %s",
                                d, _start, item.title[:50])
                    reached_boundary = True
                    break
                if d > _end:
                    skipped_oob += 1
                    proxy.sleep(max(0.1, sleep_sec * 0.3))
                    continue

                page_in_range_count += 1
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(item.to_markdown())
                stats.downloaded += 1
                stats.files.append(str(fpath))
                existing_file_stems.add(fname[:-3])
                logger.info(
                    "  [saved] %s pub=%s type=%s kws=%s (%s)",
                    item.title[:45],
                    item.pub_date,
                    item.type,
                    "|".join(item.keywords) or "(none)",
                    fname,
                )
                if convert_csv:
                    try:
                        convert_md_to_csv(fpath)
                    except Exception as e:
                        logger.warning("  [conv %s] per-file CSV failed: %s", fname, e)
                # Auto-sleep handled by proxy.get()/post()

            if reached_boundary:
                logger.info("  reached boundary at page %d", page)
                break
            if page_in_range_count == 0 and page > 3:
                logger.info("  no in-range items on page %d, stopping", page)
                break

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        skipped_out_of_range=skipped_oob,
        out_dir=str(out_dir),
        start_date=str(_start),
        end_date=str(_end),
    )
    logger.info(
        "Done PBoC OMA. downloaded=%d skipped_cached=%d skipped_oob=%d failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, skipped_oob, stats.failed, out_dir,
    )

    if build_csv:
        try:
            csv_counts = build_oma_combined_csv(out_dir)
            summary["csv"] = csv_counts
        except Exception as e:
            logger.error("build_oma_combined_csv failed: %s", e)

    return summary


def reparse_existing_files(
    out_dir: Path,
    convert_csv: bool = True,
    build_csv: bool = True,
) -> dict:
    """Re-parse raw body from existing .md files without re-downloading."""
    files = sorted(out_dir.glob(f"{OMA_FILE_PREFIX}_*.md"))
    n_total = len(files)
    n_ok = n_skip = n_fail = n_csv_ok = 0
    print(f"[REPARSE] scanning {n_total} .md files in {out_dir}", flush=True)

    for fpath in files:
        try:
            text = fpath.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [ERROR] cannot read {fpath.name}: {e}", flush=True)
            n_fail += 1
            continue
        if not text.startswith("---"):
            n_skip += 1
            continue
        fm_end = text.find("\n---", 3)
        if fm_end < 0:
            n_skip += 1
            continue
        fm_text = text[3:fm_end].strip()
        body_section = text[fm_end + 4:]

        fm: Dict[str, str] = {}
        for line in fm_text.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                fm[key.strip()] = val.strip().strip("'\"")

        if fm.get("status") == "empty":
            n_skip += 1
            continue

        # Extract raw body from ``` ... ``` block
        raw_start = body_section.find("```")
        if raw_start < 0:
            n_skip += 1
            continue
        raw_end = body_section.find("```", raw_start + 3)
        if raw_end < 0:
            n_skip += 1
            continue
        raw_body = body_section[raw_start + 3:raw_end].strip()

        pub_date = fm.get("pub_date", "")
        if not pub_date:
            n_skip += 1
            continue

        item = OmaAnnouncementItem(
            title=fm.get("title", ""),
            detail_url=fm.get("detail_url", ""),
            pub_date=pub_date,
            serial_year=fm.get("serial_year") or None,
            serial_no=fm.get("serial_no") or None,
        )
        # Re-classify type + keywords from the raw body
        classification_text = f"{item.title}\n{raw_body}"
        item.type, item.keywords = classify_announcement(classification_text)
        item.raw_body = raw_body
        if not item.keywords:
            item.parse_warnings.append("no keywords matched")

        mslug = RE_DETAIL_SLUG.search(item.detail_url)
        if mslug:
            item.detail_slug = mslug.group(1)

        fpath.write_text(item.to_markdown(), encoding="utf-8")
        n_ok += 1
        if convert_csv:
            try:
                if convert_md_to_csv(fpath):
                    n_csv_ok += 1
            except Exception as e:
                print(f"  [WARN] CSV failed for {fpath.name}: {e}", flush=True)

    print(f"[REPARSE] done: {n_ok} re-parsed, {n_skip} skipped, {n_fail} failed "
          f"({n_csv_ok} per-file CSVs written)", flush=True)

    result = {"total": n_total, "ok": n_ok, "skipped": n_skip,
              "failed": n_fail, "csv_files": n_csv_ok}
    if build_csv:
        try:
            result["combined_csv"] = build_oma_combined_csv(out_dir)
        except Exception as e:
            print(f"  [ERROR] build_oma_combined_csv failed: {e}", flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download and parse PBoC OMA news")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start date (YYYY-MM-DD). Default: 2020-01-01")
    parser.add_argument("--end-date", type=str, default=None,
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--years", type=int, default=None,
                        help="Lookback years (alternative to --start-date)")
    parser.add_argument("--reparse", action="store_true",
                        help="Re-parse existing .md files from raw body (no download)")
    parser.add_argument("--no-convert-csv", action="store_true", default=False,
                        help="Skip per-file CSV conversion")
    parser.add_argument("--no-build-csv", action="store_true", default=False,
                        help="Skip building combined oma_combined.csv / oma_keywords.csv")
    args = parser.parse_args()

    convert_csv = not args.no_convert_csv
    build_csv = not args.no_build_csv

    if args.reparse:
        out_dir = resolve_out_dir(str(Path(__file__).resolve()), "pboc_oma_news", None)
        print(reparse_existing_files(out_dir, convert_csv=convert_csv, build_csv=build_csv))
    else:
        print(download_pboc_oma_news(
            start_date=args.start_date,
            end_date=args.end_date,
            years=args.years,
            convert_csv=convert_csv,
            build_csv=build_csv,
        ))
