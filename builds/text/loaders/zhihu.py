"""builds.text.loaders.zhihu — Zhihu Q&A answers, articles + comments.

One JSON per (keyword, target_date) under ``temps/zhihu_news/``; each item
is an answer/article (Title / ContentText / Url / EditTime unix seconds,
Asia/Shanghai) and the artifact's ``comments`` key carries the per-item
comment blocks of ``downloads --with-comments`` runs.
"""
from __future__ import annotations

import datetime
import glob
import json
import logging
import os
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from builds.text import paths
from builds.text.loaders._parsing import clean_author, parse_date_str

logger = logging.getLogger(__name__)

SOURCE = "zhihu"

_SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        target_date = parse_date_str(data.get("target_date"))
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
            author = clean_author(it.get("AuthorName"))
            content_id = (it.get("ContentID") or "").strip()
            try:
                votes = int(it.get("VoteUpCount"))
            except (TypeError, ValueError):
                votes = None
            item_records.append({
                "title": title,
                "content": content,
                "date": pub_date,
                "source": SOURCE,
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
                    "source": SOURCE,
                    "author": c.get("author"),
                    "content": c.get("content"),
                    "date": parse_date_str(c.get("date")),
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
