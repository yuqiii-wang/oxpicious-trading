"""builds.text.loaders._parsing — Shared pure parsing helpers.

Helpers used by more than one source loader: date extraction, author
cleaning, downloader-CSV reading and the pseudo-YAML frontmatter + body
parsing of the per-article ``.md`` files (gov articles + pboc
announcements). No DB, no network.
"""
from __future__ import annotations

import csv
import datetime
import glob
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_date_str(s: Any) -> Optional[datetime.date]:
    """Parse 'YYYY-MM-DD' or 'YYYY/MM/DD' (possibly embedded in longer text)
    → date, or None."""
    if not s:
        return None
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(s))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def clean_author(v: Optional[str]) -> Optional[str]:
    """Trim whitespace + invisible separators (zero-width space, BOM)."""
    if not v:
        return None
    v = re.sub(r"[\u200b\u200c\u200d\ufeff\u200e\u200f]", "", v).strip()
    return v or None


def author_from_date_raw(v: Optional[str]) -> Optional[str]:
    """Extract the 来源 (origin) from a gov article's repr-quoted date_raw
    frontmatter value (e.g. '2020-01-01 14:52\\n来源： \\n 新华社\\n字号：…').
    The repr() escapes render as literal backslash-n sequences in the file."""
    if not v or "来源" not in v:
        return None
    m = re.search(r"来源：(.*)", v, re.DOTALL)
    if not m:
        return None
    parts = [p.strip() for p in re.split(r"\\n|\n", m.group(1)) if p.strip()]
    return clean_author(parts[0]) if parts else None


def read_csv_rows(path: str) -> List[Dict[str, str]]:
    """Read a downloader CSV (utf-8-sig: the writers emit a BOM)."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------
# .md frontmatter + body parsing (gov articles + pboc announcements)
# ----------------------------------------------------------------------------
_FM_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def parse_frontmatter(md_text: str) -> Dict[str, str]:
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


def parse_body_section(md_text: str, header: str, fenced: bool) -> Optional[str]:
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


def load_md_articles(
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
        fm = parse_frontmatter(md_text)
        title = (fm.get("title") or "").strip()
        pub_date = parse_date_str(fm.get("pub_date"))
        if not title or pub_date is None:
            logger.warning("    [%s] skipped %s (missing title/pub_date)",
                           source, os.path.basename(md_path))
            continue
        rows.append({
            "title": title,
            "content": parse_body_section(md_text, body_header, fenced=fenced),
            "date": pub_date,
            "source": source,
            "url": (fm.get("detail_url") or fm.get("url") or "").strip() or None,
            "author": author_from_date_raw(fm.get("date_raw")),
        })
    return rows
