"""download_pboc_lpr_news.py — Download and parse PBoC LPR (Loan Prime Rate)
announcement pages from www.pbc.gov.cn.

Source list page:
    https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/3876551/index.html

LPR is published monthly on the 20th (or next business day if holiday) since
the new formation mechanism started on 2019-08-20. Each announcement body
follows a fixed template:

    中国人民银行授权全国银行间同业拆借中心公布，2026年7月20日贷款市场报价
    利率（LPR）为：1年期LPR为3.0%，5年期以上LPR为3.5%。以上LPR在下一次
    发布LPR之前有效。

This script mirrors download_pboc_repo_news.py's workflow (paginated list
fetch → per-detail fetch → .md front-matter → per-file CSV → combined CSV)
but is much simpler because LPR has only two tenors (1Y, 5Y+) with no
quantity/serial fields.

Output:
    temp_data/analysis_output/pboc_lpr_news/pboc_lpr_<date>_<slug>.md
    temp_data/analysis_output/pboc_lpr_news/pboc_lpr_<date>_<slug>.csv
    temp_data/analysis_output/pboc_lpr_news/lpr_combined.csv

Usage:
    python download_pboc_lpr_news.py
    python download_pboc_lpr_news.py --start-date 2024-01-01
    python download_pboc_lpr_news.py --reparse   # re-parse existing .md files
"""
from __future__ import annotations

import argparse
import csv as _csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from downloads._common.core import (
    COMMON_BASE_HEADERS,
    DEFAULT_TIMEOUT,
    DEFAULT_START_DATE,
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
    parse_chinese_date,
    _clean_text,
)


PBOC_BASE = "https://www.pbc.gov.cn"

# PBoC LPR announcement list page (货币政策司 → 利率 → 贷款市场报价利率)
LPR_LIST_BASE = "/zhengcehuobisi/125207/125213/125440/3876551/"
LPR_FILE_PREFIX = "pboc_lpr"
LPR_CATEGORY = "lpr"

PBOC_MIN_VALID_BYTES = 200
SLEEP_SEC = 5.0
EMPTY_PLACEHOLDER_SUFFIX = "_empty.md"

# Paging tag detection — PBoC list pages use a JS-generated paging URL like
# `<a href=".../index_2.html">` or a templated slug. Same regex as repo news.
RE_PAGING_TAG = re.compile(r"""tagname=(['"])([^'"]*/(\w+)-(\d+)\.html)\1""")

# LPR body parsing — the body always contains both tenor rates in this form:
#   "1年期LPR为3.0%，5年期以上LPR为3.5%"
# We match liberally up to the next % sign.
RE_LPR_1Y = re.compile(r"1年期[^%]*?([\d.]+)\s*%")
RE_LPR_5Y = re.compile(r"5年期以上[^%]*?([\d.]+)\s*%")
# Fallback: "1年期3.0%" / "5年期以上3.5%" without the "LPR为" infix
RE_LPR_1Y_LOOSE = re.compile(r"1年期[^\d]*?([\d.]+)")
RE_LPR_5Y_LOOSE = re.compile(r"5年期以上[^\d]*?([\d.]+)")


logger = setup_logger("pboc_lpr_news")


# ============================================================================
# Data classes
# ============================================================================
@dataclass
class LprAnnouncementItem:
    title: str
    detail_url: str
    list_page: int = 0
    pub_date: Optional[str] = None
    detail_slug: Optional[str] = None
    lpr_1y: Optional[float] = None
    lpr_5y: Optional[float] = None
    raw_body: str = ""
    parse_warnings: List[str] = field(default_factory=list)

    def md_filename(self) -> str:
        d = self.pub_date or "00000000"
        slug = self.detail_slug or "x"
        return f"{LPR_FILE_PREFIX}_{d}_{slug}.md"

    def to_markdown(self) -> str:
        lines = [
            "---",
            f"category: {LPR_CATEGORY}",
            f"title: {self.title}",
            f"detail_url: {self.detail_url}",
            f"pub_date: {self.pub_date or ''}",
            f"lpr_1y: {'' if self.lpr_1y is None else f'{self.lpr_1y:g}'}",
            f"lpr_5y: {'' if self.lpr_5y is None else f'{self.lpr_5y:g}'}",
        ]
        if self.parse_warnings:
            lines.append(f"parse_warnings: {self.parse_warnings!r}")
        lines += [
            "---",
            "",
            f"# {self.title}",
            "",
            f"- Pub date: **{self.pub_date or 'n/a'}**",
            f"- Detail: {self.detail_url}",
            f"- 1Y LPR: {'' if self.lpr_1y is None else f'{self.lpr_1y:g}%'}",
            f"- 5Y+ LPR: {'' if self.lpr_5y is None else f'{self.lpr_5y:g}%'}",
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
PBOC_LPR_CSV_COLUMNS = [
    "pub_date",
    "title",
    "detail_url",
    "lpr_1y",
    "lpr_5y",
    "source_file",
]


# ============================================================================
# Date estimation from title (re-uses repo-news regexes)
# ============================================================================
def estimate_item_date(item: LprAnnouncementItem) -> Optional[date]:
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
    fname = f"{LPR_FILE_PREFIX}_{d.strftime('%Y-%m-%d')}_empty.md"
    fpath = out_dir / fname
    if not fpath.exists():
        content = (
            f"---\ncategory: {LPR_CATEGORY}\n"
            f"pub_date: {d.strftime('%Y-%m-%d')}\nstatus: empty\n---\n"
            f"# No LPR announcement for {d.strftime('%Y-%m-%d')}\n\n"
            f"No PBoC LPR announcement found for this date.\n"
        )
        fpath.write_text(content, encoding="utf-8")


# ============================================================================
# Body parsing
# ============================================================================
def parse_lpr_body(item: LprAnnouncementItem, html: str) -> None:
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
            if len(txt) > 30 and ("LPR" in txt or "贷款市场报价利率" in txt):
                content_div = c
                break
    if content_div is None:
        content_div = soup.body or soup

    body_text = _clean_text(content_div.get_text("\n", strip=False))
    item.raw_body = body_text

    # Collapse split numbers like "3 . 0 %" → "3.0%"
    flat = re.sub(r"\s+", "", body_text)

    # --- 1Y rate ---------------------------------------------------------
    m1 = RE_LPR_1Y.search(flat)
    if m1:
        try:
            item.lpr_1y = float(m1.group(1))
        except ValueError:
            pass
    if item.lpr_1y is None:
        m1 = RE_LPR_1Y_LOOSE.search(flat)
        if m1:
            try:
                v = float(m1.group(1))
                if 0.5 <= v <= 10.0:
                    item.lpr_1y = v
            except ValueError:
                pass
    if item.lpr_1y is None:
        item.parse_warnings.append("lpr_1y not parsed")

    # --- 5Y rate ---------------------------------------------------------
    m5 = RE_LPR_5Y.search(flat)
    if m5:
        try:
            item.lpr_5y = float(m5.group(1))
        except ValueError:
            pass
    if item.lpr_5y is None:
        m5 = RE_LPR_5Y_LOOSE.search(flat)
        if m5:
            try:
                v = float(m5.group(1))
                if 0.5 <= v <= 12.0:
                    item.lpr_5y = v
            except ValueError:
                pass
    if item.lpr_5y is None:
        item.parse_warnings.append("lpr_5y not parsed")

    # --- detail slug -----------------------------------------------------
    mslug = RE_DETAIL_SLUG.search(item.detail_url)
    if mslug:
        item.detail_slug = mslug.group(1)


# ============================================================================
# List-page fetching & pagination (adapted from repo news)
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
        return PBOC_BASE + LPR_LIST_BASE + "index.html"
    if page_prefix_fmt:
        return PBOC_BASE + LPR_LIST_BASE + page_prefix_fmt.format(page=page)
    return PBOC_BASE + LPR_LIST_BASE + f"index_{page}.html"


def _looks_like_lpr_link(text: str) -> bool:
    """Filter list-page <a> links: keep only LPR-related announcements.

    LPR titles look like "2026年7月20日贷款市场报价利率" — they contain
    "贷款市场报价利率" or "LPR". We also keep "利率" + a date pattern as a
    fallback in case the title is shorter (e.g. "LPR公告").
    """
    if not text:
        return False
    if "贷款市场报价利率" in text or "LPR" in text:
        return True
    # Fallback: title contains "利率" and a year-month-day pattern
    if "利率" in text and RE_TITLE_DATE.search(text):
        return True
    return False


def fetch_list_page(
    session: requests.Session,
    page: int,
    page_prefix_fmt: Optional[str] = None,
    proxy: Optional[AntiBotProxy] = None,
) -> Tuple[List[LprAnnouncementItem], Optional[str]]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))

    url = list_page_url(page, page_prefix_fmt)
    logger.info("Fetching LPR list page %d (%s)", page, url)

    resp = proxy.get(
        session,
        url,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[list lpr p{page}]",
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

    # The list page URL itself (e.g. .../3876551/index.html) matches
    # RE_DETAIL_SLUG because the directory "3876551" is 7 digits. We must
    # exclude it so we don't fetch the list page as if it were an announcement.
    list_page_full_url = PBOC_BASE + LPR_LIST_BASE + "index.html"

    soup = BeautifulSoup(html, "html.parser")
    items: List[LprAnnouncementItem] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href:
            continue
        if href.startswith("./"):
            href = href[2:]
        if not href.startswith("http"):
            if not href.startswith("/"):
                href = LPR_LIST_BASE + href
            href = PBOC_BASE + href
        text = a.get_text(strip=True).strip('"“”').strip()
        if not text:
            continue
        # Skip obvious navigation / boilerplate
        if text in {"公告信息", "中国人民银行", "货币政策司", "首页", "上一页", "下一页", "尾页"}:
            continue
        # Skip the list page itself (it matches RE_DETAIL_SLUG but isn't an announcement)
        if href == list_page_full_url or href.rstrip("/").endswith(LPR_LIST_BASE.rstrip("/")):
            continue
        if not RE_DETAIL_SLUG.search(href):
            continue
        if not _looks_like_lpr_link(text):
            continue

        pub_date = None
        next_span = a.find_next("span")
        if next_span:
            span_text = next_span.get_text(strip=True)
            if re.match(r"\d{4}-\d{2}-\d{2}", span_text):
                pub_date = span_text
        if pub_date is None:
            m = re.search(r"/(\d{8})\d*/index\.html$", href)
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%Y%m%d").date()
                    pub_date = d.strftime("%Y-%m-%d")
                except ValueError:
                    pass

        items.append(
            LprAnnouncementItem(
                title=text,
                detail_url=href,
                list_page=page,
                pub_date=pub_date,
            )
        )

    # Dedup by detail_url (links can appear twice on the page)
    dedup: Dict[str, LprAnnouncementItem] = {}
    for it in items:
        dedup[it.detail_url] = it
    return list(dedup.values()), detected_prefix


def smart_pagination_pages(
    session: requests.Session,
    target_start: date,
    max_pages: int = 50,
    jump_interval: int = 5,
    proxy: Optional[AntiBotProxy] = None,
) -> Tuple[List[int], Optional[str]]:
    """Walk LPR list pages until the oldest entry on a page predates target_start.

    LPR is monthly so the total page count is small (~5 pages for the full
    2019-2026 history); we still use a jump+incremental search to avoid
    fetching every page when only recent dates are missing.
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

        # Binary-search-ish: walk incrementally from last_in_range_page+1
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
    session: requests.Session, item: LprAnnouncementItem,
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
    parse_lpr_body(item, resp.text)
    return True


# ============================================================================
# CSV conversion
# ============================================================================
def _parse_lpr_fm_simple(text: str) -> Dict[str, str]:
    """Parse YAML front-matter of an LPR .md file into a flat dict (stdlib)."""
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
    """Parse a single LPR .md file → per-file CSV. Returns True if a row was written."""
    if csv_path is None:
        csv_path = md_path.with_suffix(".csv")
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[conv %s] cannot read: %s", md_path.name, e)
        return False

    fm = _parse_lpr_fm_simple(text)
    pub_date = fm.get("pub_date", "")
    if not pub_date or fm.get("status") == "empty":
        return False

    # Skip files where neither rate was parsed (avoid writing empty rows)
    lpr_1y_raw = fm.get("lpr_1y", "")
    lpr_5y_raw = fm.get("lpr_5y", "")
    if not lpr_1y_raw and not lpr_5y_raw:
        return False

    parse_warnings_raw = fm.get("parse_warnings", "")
    if parse_warnings_raw.startswith("[") and parse_warnings_raw.endswith("]"):
        parse_warnings = parse_warnings_raw[1:-1].strip()
    else:
        parse_warnings = parse_warnings_raw

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=PBOC_LPR_CSV_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "pub_date":     pub_date,
            "title":        fm.get("title", ""),
            "detail_url":   fm.get("detail_url", ""),
            "lpr_1y":       lpr_1y_raw,
            "lpr_5y":       lpr_5y_raw,
            "source_file":  md_path.name,
        })
    return True


def build_lpr_combined_csv(md_dir: Path, output_dir: Optional[Path] = None) -> Dict[str, int]:
    """Aggregate all pboc_lpr_*.md (non-empty) files into lpr_combined.csv.

    Prefers reading existing per-file CSVs (fast); falls back to parsing .md
    files when a per-file CSV is missing.
    """
    if output_dir is None:
        output_dir = md_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    combined_path = output_dir / "lpr_combined.csv"

    md_files = sorted(md_dir.glob(f"{LPR_FILE_PREFIX}_*.md"))
    md_files = [f for f in md_files if not f.name.endswith(EMPTY_PLACEHOLDER_SUFFIX)]
    csv_files = sorted(md_dir.glob(f"{LPR_FILE_PREFIX}_*.csv"))

    logger.info("[build-csv] scanning %s: %d .md files, %d per-file CSVs",
                md_dir, len(md_files), len(csv_files))

    counts = {"rows": 0, "files_ok": 0, "files_empty": 0, "files_failed": 0}

    with open(combined_path, "w", encoding="utf-8-sig", newline="") as fout:
        writer = _csv.DictWriter(fout, fieldnames=PBOC_LPR_CSV_COLUMNS)
        writer.writeheader()

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
                out_row = {col: row.get(col, "") for col in PBOC_LPR_CSV_COLUMNS}
                writer.writerow(out_row)
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
                writer.writerow({col: row.get(col, "") for col in PBOC_LPR_CSV_COLUMNS})
                counts["rows"] += 1
            counts["files_ok"] += 1

    logger.info("[build-csv] saved %s (%d rows, %d ok, %d empty, %d failed)",
                combined_path, counts["rows"], counts["files_ok"],
                counts["files_empty"], counts["files_failed"])
    return counts


# ============================================================================
# Main download entry point
# ============================================================================
def download_pboc_lpr_news(
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
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "pboc_lpr_news", out_root)

    # LPR started 2019-08-20 under the new mechanism; default to project-wide
    # DEFAULT_START_DATE (2020-01-01) for a consistent backfill horizon.
    if start_date is None and years is not None:
        _start, _end = parse_date_window(
            end_date=end_date, start_date=None, lookback_years=years,
        )
    else:
        if start_date is None:
            start_date = DEFAULT_START_DATE
        _start, _end = parse_date_window(
            end_date=end_date, start_date=start_date, lookback_years=None,
        )

    session = build_session()
    stats = RunStats()
    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
        # LPR is a small dataset (~5 list pages, ~89 records total). Pagination
        # probing naturally hits 404 on non-existent pages; we don't want that
        # to mark the host as blocked. Real anti-bot 403/429 responses are
        # rare for this endpoint.
        enable_host_tracking=False,
    )
    proxy = AntiBotProxy(proxy_config)

    cached_dates = scan_present_dates_with_pattern(
        out_dir, prefixes=[LPR_FILE_PREFIX], min_bytes=100, ext_glob="*.md",
    ).get(LPR_FILE_PREFIX, set())
    stats.skipped_cached = len(cached_dates)
    cached_earliest = min(cached_dates) if cached_dates else None
    cached_latest = max(cached_dates) if cached_dates else None
    if cached_dates:
        logger.info("[lpr] %d dates already cached, earliest=%s, latest=%s",
                    len(cached_dates), cached_earliest, cached_latest)
    else:
        logger.info("[lpr] no prior cached files")

    logger.info("Starting PBoC LPR download: %s -> %s", _start, _end)

    skipped_oob = 0
    try:
        # Decide page strategy: if history is already covered, we only need
        # page 1 to pick up any new recent announcements. Only walk multiple
        # pages when history dates are actually missing — this avoids probing
        # pages 2..N (which 404 on PBoC's small LPR list) when the backfill
        # is already complete.
        #
        # LPR is published monthly on the 20th, so the first cached LPR date
        # at or after _start (e.g. _start=2020-01-01) is up to ~50 days later
        # (e.g. 2020-01-20, or 2020-02-20 if _start is late in a month). Use
        # a 60-day buffer so "history covered" tolerates the publication
        # schedule instead of strictly requiring cached_earliest <= _start.
        HISTORY_COVERED_BUFFER = timedelta(days=60)
        history_covered = (
            cached_earliest is not None
            and cached_earliest <= _start + HISTORY_COVERED_BUFFER
        )
        if history_covered:
            logger.info(
                "[lpr] history covered (earliest=%s within %.0f days of %s), "
                "fetching page 1 only for recent updates",
                cached_earliest, HISTORY_COVERED_BUFFER.total_seconds() / 86400, _start,
            )
            pages_to_process = [1]
            page_prefix_fmt = None  # detected on page 1 in the main loop
        else:
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

                # Pre-fetch skip: when the list page already provided a
                # pub_date (from the adjacent <span> or the URL's 8-digit
                # date pattern), check cached_dates / out-of-range BEFORE
                # making the HTTP detail request so already-downloaded
                # dates are not re-requested.
                pre_d: Optional[date] = None
                if item.pub_date:
                    try:
                        pre_d = datetime.strptime(item.pub_date, "%Y-%m-%d").date()
                    except ValueError:
                        pre_d = None

                if pre_d is not None:
                    if pre_d in cached_dates:
                        stats.skipped_cached += 1
                        page_in_range_count += 1
                        logger.info(
                            "  [skip-cached %s] %s (no HTTP request)",
                            pre_d, item.title[:45],
                        )
                        proxy.sleep(max(0.1, sleep_sec * 0.3))
                        continue
                    if pre_d < _start:
                        skipped_oob += 1
                        logger.info("  [boundary %s < %s] stop: %s",
                                    pre_d, _start, item.title[:50])
                        reached_boundary = True
                        break
                    if pre_d > _end:
                        skipped_oob += 1
                        proxy.sleep(max(0.1, sleep_sec * 0.3))
                        continue

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

                # Post-fetch re-check: parse_lpr_body may have refined
                # item.pub_date from the detail page meta. Skip saving if
                # the refined date is already cached or out of range.
                if d in cached_dates:
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
                fname = item.md_filename()
                fpath = out_dir / fname
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(item.to_markdown())
                stats.downloaded += 1
                stats.files.append(str(fpath))
                cached_dates.add(d)
                logger.info(
                    "  [saved] %s pub=%s 1Y=%s 5Y=%s (%s)",
                    item.title[:45],
                    item.pub_date,
                    f"{item.lpr_1y:g}%" if item.lpr_1y is not None else "?",
                    f"{item.lpr_5y:g}%" if item.lpr_5y is not None else "?",
                    fname,
                )
                if convert_csv and (item.lpr_1y is not None or item.lpr_5y is not None):
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
        "Done PBoC LPR. downloaded=%d skipped_cached=%d skipped_oob=%d failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, skipped_oob, stats.failed, out_dir,
    )

    if build_csv:
        try:
            csv_counts = build_lpr_combined_csv(out_dir)
            summary["csv"] = csv_counts
        except Exception as e:
            logger.error("build_lpr_combined_csv failed: %s", e)

    return summary


def reparse_existing_files(
    out_dir: Path,
    convert_csv: bool = True,
    build_csv: bool = True,
) -> dict:
    """Re-parse raw body from existing .md files without re-downloading."""
    files = sorted(out_dir.glob(f"{LPR_FILE_PREFIX}_*.md"))
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

        # Simple key:value parse
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

        item = LprAnnouncementItem(
            title=fm.get("title", ""),
            detail_url=fm.get("detail_url", ""),
            pub_date=pub_date,
        )
        # Re-parse rates from the raw body
        flat = re.sub(r"\s+", "", raw_body)
        m1 = RE_LPR_1Y.search(flat)
        if m1:
            try:
                item.lpr_1y = float(m1.group(1))
            except ValueError:
                pass
        m5 = RE_LPR_5Y.search(flat)
        if m5:
            try:
                item.lpr_5y = float(m5.group(1))
            except ValueError:
                pass
        item.raw_body = raw_body
        if item.lpr_1y is None:
            item.parse_warnings.append("lpr_1y not parsed")
        if item.lpr_5y is None:
            item.parse_warnings.append("lpr_5y not parsed")

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
            result["combined_csv"] = build_lpr_combined_csv(out_dir)
        except Exception as e:
            print(f"  [ERROR] build_lpr_combined_csv failed: {e}", flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download and parse PBoC LPR news")
    parser.add_argument("--start-date", type=str, default=None,
                        help=f"Start date (YYYY-MM-DD). Default: {DEFAULT_START_DATE}")
    parser.add_argument("--end-date", type=str, default=None,
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--years", type=int, default=None,
                        help="Lookback years (alternative to --start-date)")
    parser.add_argument("--reparse", action="store_true",
                        help="Re-parse existing .md files from raw body (no download)")
    parser.add_argument("--no-convert-csv", action="store_true", default=False,
                        help="Skip per-file CSV conversion")
    parser.add_argument("--no-build-csv", action="store_true", default=False,
                        help="Skip building combined lpr_combined.csv")
    args = parser.parse_args()

    convert_csv = not args.no_convert_csv
    build_csv = not args.no_build_csv

    if args.reparse:
        out_dir = resolve_out_dir(str(Path(__file__).resolve()), "pboc_lpr_news", None)
        print(reparse_existing_files(out_dir, convert_csv=convert_csv, build_csv=build_csv))
    else:
        print(download_pboc_lpr_news(
            start_date=args.start_date,
            end_date=args.end_date,
            years=args.years,
            convert_csv=convert_csv,
            build_csv=build_csv,
        ))
