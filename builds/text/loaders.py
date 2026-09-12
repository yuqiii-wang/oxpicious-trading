"""builds.text.loaders — Parse the downloaded news artifacts into news rows.

Pure parsers: no DB, no network. Each loader returns a list of row dicts with
the uniform shape::

    {"title": str, "content": str | None, "date": datetime.date,
     "source": str, "url": str | None}

Sources + formats (produced by downloads.macro.*):

  load_gov_news      — temps/gov_news/: every crawled articles/*.md
                       (pseudo-YAML frontmatter + ``## Body`` markdown)
                       plus titles-only rows from gov_news_titles.csv for
                       titles never crawled (content=None).
  load_ndrc_news     — temps/ndrc_news/ndrc_news_titles.csv (titles only).
  load_pboc_*_news   — temps/pboc_{lpr,repo,oma}_news/: per-article .md with
                       frontmatter + ``## Raw body`` fenced block.
  load_zhihu_news    — temps/zhihu_news/*.json: one JSON per
                       (keyword, target_date); each item → one row, dated by
                       its EditTime (unix seconds, Asia/Shanghai).

The md frontmatter is written with ``repr()``-quoted values and is NOT valid
YAML in general (e.g. multi-line ``date_raw``), so it is parsed line-by-line
here instead of with a YAML library.
"""
from __future__ import annotations

import csv
import datetime
import glob
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from builds.text import paths

logger = logging.getLogger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")

_SOURCE_GOV = "gov"
_SOURCE_NDRC = "ndrc"
_SOURCE_PBOC_LPR = "pboc_lpr"
_SOURCE_PBOC_OMO = "pboc_omo"
_SOURCE_PBOC_OMA = "pboc_oma"
_SOURCE_ZHIHU = "zhihu"

# All sources this build knows about, in load order.
ALL_SOURCES = [
    _SOURCE_GOV, _SOURCE_NDRC, _SOURCE_PBOC_LPR,
    _SOURCE_PBOC_OMO, _SOURCE_PBOC_OMA, _SOURCE_ZHIHU,
]


def _parse_date_str(s: Any) -> Optional[datetime.date]:
    """Parse 'YYYY-MM-DD' (possibly embedded in longer text) → date, or None."""
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(s))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _clean_author(v: Optional[str]) -> Optional[str]:
    """Trim whitespace + invisible separators (zero-width space, BOM)."""
    if not v:
        return None
    v = re.sub(r"[\u200b\u200c\u200d\ufeff\u200e\u200f]", "", v).strip()
    return v or None


def _author_from_date_raw(v: Optional[str]) -> Optional[str]:
    """Extract the 来源 (origin) from a gov article's repr-quoted date_raw
    frontmatter value (e.g. '2020-01-01 14:52\\n来源： \\n 新华社\\n字号：…').
    The repr() escapes render as literal backslash-n sequences in the file."""
    if not v or "来源" not in v:
        return None
    m = re.search(r"来源：(.*)", v, re.DOTALL)
    if not m:
        return None
    parts = [p.strip() for p in re.split(r"\\n|\n", m.group(1)) if p.strip()]
    return _clean_author(parts[0]) if parts else None


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    """Read a downloader CSV (utf-8-sig: the writers emit a BOM)."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------
# .md frontmatter + body parsing (gov articles + pboc announcements)
# ----------------------------------------------------------------------------
_FM_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def _parse_frontmatter(md_text: str) -> Dict[str, str]:
    """Parse the pseudo-YAML frontmatter block of a downloader .md file.

    Handles repr-quoted values ('…' / "…") and inline lists ([a, b]). The
    first ``---``/``---`` block wins; anything before/after is ignored.
    """
    lines = md_text.splitlines()
    fm: Dict[str, str] = {}
    if not lines or lines[0].strip() != "---":
        return fm
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = _FM_LINE_RE.match(line)
        if not m:
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fm[m.group(1)] = value
    return fm


def _parse_body_section(md_text: str, header: str, fenced: bool) -> Optional[str]:
    """Extract a markdown section body after *header*.

    fenced=True  — body is inside a ``` fence (pboc '## Raw body').
    fenced=False — body runs from after the header to EOF (gov '## Body').
    """
    idx = md_text.find(header)
    if idx < 0:
        return None
    rest = md_text[idx + len(header):].lstrip("\n")
    if fenced:
        start = rest.find("```")
        if start < 0:
            return None
        end = rest.find("```", start + 3)
        body = rest[start + 3:end if end >= 0 else len(rest)]
    else:
        body = rest
    return body.strip() or None


def _load_md_articles(
    md_dir: str,
    md_glob: str,
    source: str,
    body_header: str = "## Raw body",
    fenced: bool = True,
) -> List[Dict[str, Any]]:
    """Parse per-article .md files (frontmatter title/pub_date/url + body).

    pboc announcement files carry the body in a fenced ``## Raw body`` block;
    gov article files use an open ``## Body`` section — pass
    body_header='## Body', fenced=False for those. Author comes from the gov
    frontmatter's 来源 (inside date_raw); pboc files have none (NULL).
    """
    rows: List[Dict[str, Any]] = []
    for md_path in sorted(glob.glob(os.path.join(md_dir, md_glob))):
        try:
            with open(md_path, encoding="utf-8") as f:
                md_text = f.read()
        except OSError as e:
            logger.warning("    [%s] unreadable %s: %s", source, md_path, e)
            continue
        fm = _parse_frontmatter(md_text)
        title = (fm.get("title") or "").strip()
        pub_date = _parse_date_str(fm.get("pub_date"))
        if not title or pub_date is None:
            logger.warning("    [%s] skipped %s (missing title/pub_date)",
                           source, os.path.basename(md_path))
            continue
        rows.append({
            "title": title,
            "content": _parse_body_section(md_text, body_header, fenced=fenced),
            "date": pub_date,
            "source": source,
            "url": (fm.get("detail_url") or fm.get("url") or "").strip() or None,
            "author": _author_from_date_raw(fm.get("date_raw")),
        })
    return rows


# ----------------------------------------------------------------------------
# Per-source loaders
# ----------------------------------------------------------------------------
def load_gov_news() -> List[Dict[str, Any]]:
    """gov.cn 政策解读: every crawled article .md (frontmatter + ## Body)
    PLUS every title from the titles CSV that was never crawled
    (content=None). articles_index.csv is the downloader's crawl STATE file
    (only reflects the latest incremental run), so the .md files — one per
    crawled article, self-describing via frontmatter — are the source of
    truth for content; the titles CSV guarantees full title coverage."""
    # Articles first: frontmatter carries title/pub_date/url + the body.
    articles = _load_md_articles(paths.GOV_ARTICLES_DIR, "*.md", _SOURCE_GOV,
                                 body_header="## Body", fenced=False)
    crawled_urls = {a["url"] for a in articles if a["url"]}

    rows: List[Dict[str, Any]] = list(articles)
    n_dup = 0
    for r in _read_csv_rows(paths.GOV_TITLES_CSV):
        title = (r.get("title") or "").strip()
        pub_date = _parse_date_str(r.get("pub_date"))
        if not title or pub_date is None:
            continue
        url = (r.get("url") or "").strip() or None
        if url in crawled_urls:
            n_dup += 1  # already loaded from its .md (with content)
            continue
        rows.append({
            "title": title,
            "content": None,
            "date": pub_date,
            "source": _SOURCE_GOV,
            "url": url,
            "author": None,  # titles CSV carries no 来源 — the .md rows do
        })
    if n_dup:
        logger.info("    [gov] %d crawled titles merged with their .md bodies",
                    n_dup)
    return rows


def load_ndrc_news() -> List[Dict[str, Any]]:
    """ndrc.gov.cn 新闻发布 title list — titles only (no detail crawl)."""
    rows: List[Dict[str, Any]] = []
    for r in _read_csv_rows(paths.NDRC_TITLES_CSV):
        title = (r.get("title") or "").strip()
        pub_date = _parse_date_str(r.get("pub_date"))
        if not title or pub_date is None:
            continue
        rows.append({
            "title": title,
            "content": None,
            "date": pub_date,
            "source": _SOURCE_NDRC,
            "url": (r.get("url") or "").strip() or None,
            "author": None,
        })
    return rows


def load_pboc_lpr_news() -> List[Dict[str, Any]]:
    return _load_md_articles(paths.PBOC_LPR_NEWS_DIR, "pboc_lpr_*.md",
                             _SOURCE_PBOC_LPR)


def load_pboc_omo_news() -> List[Dict[str, Any]]:
    return _load_md_articles(paths.PBOC_OMO_NEWS_DIR, "pboc_omo_*.md",
                             _SOURCE_PBOC_OMO)


def load_pboc_oma_news() -> List[Dict[str, Any]]:
    return _load_md_articles(paths.PBOC_OMA_NEWS_DIR, "pboc_oma_*.md",
                             _SOURCE_PBOC_OMA)


def _parse_zhihu_artifacts() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse temps/zhihu_news/*.json into (item_records, comment_records).

    Pre-disambiguation records: news items carry the working-copy helper keys
    (``_author`` / ``_content_id``); comment records carry ``_content_id``
    (their parent item) and are only resolved to news rows after the item
    titles are finalized — see :func:`_finalize_zhihu_titles`. Comments come
    from the artifact's top-level ``comments`` key (downloads
    ``--with-comments``): [{content_id, comments: [...]}] with each comment
    carrying the platform id, content, created date, vote_count and replies.
    """
    item_records: List[Dict[str, Any]] = []
    comment_records: List[Dict[str, Any]] = []
    for json_path in sorted(glob.glob(os.path.join(paths.ZHIHU_NEWS_DIR, "*.json"))):
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("    [zhihu] unreadable %s: %s", json_path, e)
            continue
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        target_date = _parse_date_str(data.get("target_date"))
        comments_by_cid: Dict[str, list] = {}
        raw_comments = data.get("comments")
        if isinstance(raw_comments, list):
            for entry in raw_comments:
                if isinstance(entry, dict) and (entry.get("content_id") or "").strip():
                    comments_by_cid[str(entry["content_id"]).strip()] = (
                        entry.get("comments") or [])
        for it in items:
            if not isinstance(it, dict):
                continue
            title = (it.get("Title") or "").strip()
            content = (it.get("ContentText") or "").strip() or None
            if not title or not content:
                continue
            # Prefer the item's own publish/update timestamp; the file-level
            # target_date is the (often next-day) market date being asked about.
            pub_date = None
            edit_time = it.get("EditTime")
            if edit_time:
                try:
                    pub_date = datetime.datetime.fromtimestamp(
                        int(edit_time), tz=_SHANGHAI).date()
                except (TypeError, ValueError, OSError):
                    pub_date = None
            if pub_date is None:
                pub_date = target_date
            if pub_date is None:
                continue
            author = _clean_author(it.get("AuthorName"))
            content_id = (it.get("ContentID") or "").strip()
            try:
                votes = int(it.get("VoteUpCount"))
            except (TypeError, ValueError):
                votes = None
            item_records.append({
                "title": title,
                "content": content,
                "date": pub_date,
                "source": _SOURCE_ZHIHU,
                "url": (it.get("Url") or "").strip() or None,
                "author": author,
                "votes": votes,
                "_author": author or "",  # disambiguation working copy (popped below)
                "_content_id": content_id,
            })
            for c in comments_by_cid.get(content_id, []):
                if not isinstance(c, dict):
                    continue
                try:
                    comment_id = int(c.get("comment_id"))
                except (TypeError, ValueError):
                    continue  # no platform id — cannot be PK-checked or re-loaded
                try:
                    parent_id = int(c["parent_comment_id"])
                except (TypeError, KeyError, ValueError):
                    parent_id = None
                comment_records.append({
                    "comment_id": comment_id,
                    "parent_comment_id": parent_id,
                    "source": _SOURCE_ZHIHU,
                    "author": c.get("author"),
                    "content": c.get("content"),
                    "date": _parse_date_str(c.get("date")),
                    "votes": c.get("votes"),
                    "is_reply": bool(c.get("is_reply")),
                    "_content_id": content_id,
                })
    return item_records, comment_records


def _finalize_zhihu_titles(item_records: List[Dict[str, Any]]) -> Dict[str, Tuple]:
    """Disambiguate duplicate (title, date) pairs across all items (in place).

    The raw Title is the question page title, shared by every answer of the
    same question on the same day, but text.news PKs on (title, source, date).
    The first item keeps the plain title; later duplicates are disambiguated
    with the author name (then ContentID tail) so no answer is silently lost
    to an upsert conflict. Returns {content_id: (final_title, date)} so
    comment rows can be resolved to their (possibly renamed) parent item.
    """
    used: set = set()
    title_map: Dict[str, Tuple] = {}
    for row in item_records:
        candidates = [row["title"]]
        suffixes = [row["_author"], (row["_content_id"] or "")[-6:]]
        candidates += [f"{row['title']} · {s}" for s in suffixes if s]
        base = row["title"]
        i = 2
        while True:
            candidate = next(
                (c for c in candidates if (c, row["date"]) not in used), None)
            if candidate is not None:
                break
            candidate = f"{base} · #{i}"  # author + content id both collided
            if (candidate, row["date"]) not in used:
                break
            i += 1
        row["title"] = candidate
        used.add((candidate, row["date"]))
        title_map[row["_content_id"]] = (candidate, row["date"])
    return title_map


def load_zhihu_news() -> List[Dict[str, Any]]:
    """Zhihu Q&A search results — one row per answer/article item.

    See :func:`_parse_zhihu_artifacts` / :func:`_finalize_zhihu_titles` for
    the parse and (title, date) disambiguation details; votes carry the
    item's upvote count (VoteUpCount) or NULL when absent.
    """
    item_records, _ = _parse_zhihu_artifacts()
    _finalize_zhihu_titles(item_records)
    for row in item_records:
        row.pop("_author", None)
        row.pop("_content_id", None)
    return item_records


def load_zhihu_comments() -> List[Dict[str, Any]]:
    """Zhihu comments — one row per platform comment (root + embedded reply).

    Parent items are resolved through the finalized (title, date) pairs, so a
    comment follows its answer even when disambiguation renamed the title.
    Rows whose parent item was dropped (no content etc.) are skipped, and
    in-batch PK duplicates (the same answer returned by several questions)
    are deduped here — a duplicated PK inside one upsert batch would raise
    "cannot affect row a second time". The DB-side PK check happens in
    builds.text.upsert (fetch_existing_comment_pks / filter_new_comments).
    """
    item_records, comment_records = _parse_zhihu_artifacts()
    title_map = _finalize_zhihu_titles(item_records)
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for c in comment_records:
        parent = title_map.get(c["_content_id"])
        if parent is None or c["comment_id"] in seen:
            continue
        seen.add(c["comment_id"])
        rows.append({
            "comment_id": c["comment_id"],
            "parent_comment_id": c["parent_comment_id"],
            "source": c["source"],
            "title": parent[0],          # parent news row natural key …
            "parent_date": parent[1],    # … resolved to news_id at upsert
            "author": c["author"],
            "content": c["content"],
            "date": c["date"],
            "votes": c["votes"],
            "is_reply": c["is_reply"],
        })
    return rows


# Per-source loader registry (order matters only for log readability).
LOADERS = {
    _SOURCE_GOV: load_gov_news,
    _SOURCE_NDRC: load_ndrc_news,
    _SOURCE_PBOC_LPR: load_pboc_lpr_news,
    _SOURCE_PBOC_OMO: load_pboc_omo_news,
    _SOURCE_PBOC_OMA: load_pboc_oma_news,
    _SOURCE_ZHIHU: load_zhihu_news,
}


def load_news(sources: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Load + merge news rows for *sources* (default: all), deduped on the
    text.news natural key (title, source, date) — last occurrence wins."""
    wanted = sources or ALL_SOURCES
    unknown = [s for s in wanted if s not in LOADERS]
    if unknown:
        raise ValueError(f"unknown sources: {unknown} (known: {ALL_SOURCES})")

    merged: Dict[tuple, Dict[str, Any]] = {}
    for source in wanted:
        rows = LOADERS[source]()
        logger.info("    [%s] parsed %d rows", source, len(rows))
        for row in rows:
            merged[(row["title"], row["source"], row["date"])] = row
    return list(merged.values())
