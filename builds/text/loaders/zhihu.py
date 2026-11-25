"""builds.text.loaders.zhihu — Zhihu Q&A answers, articles + comments.

One JSON per (keyword, target_date) under ``temps/zhihu_news/``; each item
is an answer/article (Title / ContentText / Url / EditTime unix seconds,
Asia/Shanghai) and the artifact's ``comments`` key carries the per-item
comment blocks of ``downloads --with-comments`` runs.

The SAME answer is routinely captured by several artifacts — the
``tmr_forcast`` file fetched the day before and the ``tday_snapshot`` /
per-date lookback file fetched on the day share the target date, and
one-off question searches add one artifact per fetch day — so items are
deduped by ContentID (fallback: URL without its query string) across all
artifacts, and titles are disambiguated against the rows already stored in
text.news (``stored_rows``, passed by the build). Both halves guard the
duplicate-row failure mode: without the dedup a repeated copy was renamed
into a second text.news row (``title · author``), and without the claim a
shifted parse order renamed already-stored answers into NEW titles.
"""
from __future__ import annotations

import datetime
import glob
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from builds.text import paths
from builds.text.loaders._parsing import clean_author, parse_date_str, url_answer_id

logger = logging.getLogger(__name__)

SOURCE = "zhihu"

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# Working keys on item records (popped before the rows leave the loader):
#   _author      — disambiguation working copy of the author
#   _content_id  — platform answer/article id (dedup key + comment parent)
#   _edit_time   — int EditTime (unix seconds) or None; merge freshness
_WORK_KEYS = ("_author", "_content_id", "_edit_time")


def _merge_answer_copy(keep: Dict[str, Any], dup: Dict[str, Any]) -> None:
    """Fold a later artifact copy of an already-seen answer into *keep*.

    The first occurrence keeps its position (parse order must stay stable —
    the title disambiguation assigns candidates in record order); *dup* wins
    field-by-field only when it is strictly fresher (larger EditTime — the
    answer was edited between the two fetches).
    """
    keep_ts, dup_ts = keep["_edit_time"], dup["_edit_time"]
    if dup_ts is None or (keep_ts is not None and dup_ts <= keep_ts):
        return
    for field in ("title", "content", "date", "url", "author", "votes"):
        keep[field] = dup[field]
    keep["_author"] = dup["_author"]
    keep["_edit_time"] = dup_ts


def _parse_zhihu_artifacts() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse temps/zhihu_news/*.json into (item_records, comment_records).

    Records are deduped ACROSS artifacts by ContentID (fallback: URL without
    its query string — the utm_* params vary): the first occurrence is kept
    in place and a later copy only refreshes its fields when its EditTime is
    newer (:func:`_merge_answer_copy`). Comment records carry ``_content_id``
    (their parent item) and are only resolved to news rows after the item
    titles are finalized — see :func:`_finalize_zhihu_titles`; copies of the
    same answer contribute their own comment blocks (deduped downstream by
    comment_id, first occurrence wins). Comments come from the artifact's
    top-level ``comments`` key (downloads ``--with-comments``):
    [{content_id, comments: [...]}] with each comment carrying the platform
    id, content, created date, vote_count and replies.
    """
    item_records: List[Dict[str, Any]] = []
    comment_records: List[Dict[str, Any]] = []
    record_by_key: Dict[str, int] = {}
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
            edit_time: Optional[int]
            try:
                edit_time = int(it.get("EditTime"))
            except (TypeError, ValueError):
                edit_time = None
            pub_date = None
            if edit_time:
                try:
                    pub_date = datetime.datetime.fromtimestamp(
                        edit_time, tz=_SHANGHAI).date()
                except (TypeError, ValueError, OSError):
                    pub_date = None
            if pub_date is None:
                pub_date = target_date
            if pub_date is None:
                continue
            author = clean_author(it.get("AuthorName"))
            url = (it.get("Url") or "").strip() or None
            content_id = str(
                it.get("ContentID") if it.get("ContentID") is not None else ""
            ).strip()
            try:
                votes = int(it.get("VoteUpCount"))
            except (TypeError, ValueError):
                votes = None
            record = {
                "title": title,
                "content": content,
                "date": pub_date,
                "source": SOURCE,
                "url": url,
                "author": author,
                "votes": votes,
                "_author": author or "",  # disambiguation working copy
                "_content_id": content_id,
                "_edit_time": edit_time,
            }
            # Cross-artifact dedup: ContentID first, else the URL's path
            # (utm_* query params differ per capture). An item with neither
            # stays undeduped — it cannot be identified across artifacts.
            dedup_key = content_id or (url.split("?", 1)[0] if url else "")
            if dedup_key:
                first = record_by_key.get(dedup_key)
                if first is not None:
                    _merge_answer_copy(item_records[first], record)
                else:
                    record_by_key[dedup_key] = len(item_records)
                    item_records.append(record)
            else:
                item_records.append(record)
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


def _finalize_zhihu_titles(
    item_records: List[Dict[str, Any]],
    stored_rows: Optional[Dict[Tuple[str, datetime.date], Dict[str, Any]]] = None,
) -> Dict[str, Tuple]:
    """Disambiguate duplicate (title, date) pairs across all items (in place).

    The raw Title is the question page title, shared by every answer of the
    same question on the same day, but text.news PKs on (title, source, date).
    The first item keeps the plain title; later duplicates are disambiguated
    with the author name (then ContentID tail) so no answer is silently lost
    to an upsert conflict.

    *stored_rows* — {(title, date): {author, url, news_id}} over the zhihu
    rows already stored in text.news (built by the build from the pre-parse
    DB state). A record first tries to CLAIM a stored row, so a re-run
    reproduces the stored titles exactly instead of re-shuffling them (the
    assignment order depends on the parsed artifact set, which drifts as
    lookback files refresh and question searches accumulate; without the
    claim a shifted order renamed already-stored answers into NEW titles and
    re-created duplicates):

      1. answer-id claim — the stored row whose URL carries the same
         /answer/<id> (or /p/<id>) tail as this record's URL. The platform
         id is the only stable identity: AuthorName vanishes/reappears
         between captures, the question title gets renamed on Zhihu, and
         EditTime can cross midnight (date drift) — any of those breaks a
         title/author match and would spawn a fresh row PER RUN for the
         same answer. The claim anchors BOTH the stored title AND the
         stored date; among a stored answer's rows (pre-cleanup pairs) the
         plain-title / lowest-news_id row wins — exactly the row the
         one-time duplicate cleanup keeps, so build + cleanup converge.
      2. title-candidate claim — a candidate title (plain, ``· author``,
         ``· ContentID tail``) already stored for this date under the same
         author; covers rows whose URL is absent or unparseable. Stored
         ``· #N`` variants (author + ContentID tail both collided when the
         row was first stored) are claimed too, via the (date, author)
         index.

    Unclaimed records assign greedily, skipping BOTH in-batch used pairs and
    stored pairs so a new answer can never steal a stored row's key.
    Returns {content_id: (final_title, date)} so comment rows can be
    resolved to their (possibly renamed) parent item.
    """
    stored = stored_rows or {}
    stored_author = {key: (entry.get("author") or None)
                     for key, entry in stored.items()}
    stored_by_answer: Dict[str, List[Tuple[str, datetime.date, int]]] = {}
    # (date, author) → stored titles, for the · #N claim fallback.
    stored_by_date_author: Dict[Tuple, List[str]] = {}
    for (st, sd), entry in stored.items():
        aid = url_answer_id(entry.get("url"))
        if aid:
            stored_by_answer.setdefault(aid, []).append(
                (st, sd, entry.get("news_id") or 0))
        stored_by_date_author.setdefault((sd, entry.get("author") or None),
                                         []).append(st)
    # Claim priority == the duplicate-cleanup keeper choice: plain title
    # first, then lowest news_id.
    for entries in stored_by_answer.values():
        entries.sort(key=lambda e: (0 if " · " not in e[0] else 1, e[2]))
    for titles in stored_by_date_author.values():
        titles.sort()

    used: set = set()
    title_map: Dict[str, Tuple] = {}
    for row in item_records:
        base = row["title"]
        author = row["_author"] or None
        candidates = [base]
        candidates += [f"{base} · {s}" for s in
                       (row["_author"], (row["_content_id"] or "")[-6:]) if s]
        claimed: Optional[Tuple[str, datetime.date]] = None
        # 1. answer-id claim (author / question-title / date drift tolerated)
        for st, sd, _nid in stored_by_answer.get(
                url_answer_id(row.get("url")) or "", []):
            if (st, sd) not in used:
                claimed = (st, sd)
                break
        # 2. title-candidate claim under the same author (same date only)
        if claimed is None:
            date = row["date"]
            candidate = next(
                (c for c in candidates
                 if (c, date) in stored_author
                 and stored_author[(c, date)] == author
                 and (c, date) not in used),
                None)
            if candidate is None:
                # stored '· #N' variants share neither the plain base nor a
                # candidate slot — claim via the (date, author) index.
                candidate = next(
                    (t for t in stored_by_date_author.get((date, author), [])
                     if t.startswith(f"{base} · ") and (t, date) not in used),
                    None)
            if candidate is not None:
                claimed = (candidate, date)
        if claimed is not None:
            candidate, date = claimed
            row["title"] = candidate
            row["date"] = date  # anchored: date drift must not spawn a row
        else:
            date = row["date"]
            i = 2
            while True:
                candidate = next(
                    (c for c in candidates
                     if (c, date) not in used and (c, date) not in stored), None)
                if candidate is not None:
                    break
                candidate = f"{base} · #{i}"  # author + content id both collided
                if ((candidate, date) not in used
                        and (candidate, date) not in stored):
                    break
                i += 1
            row["title"] = candidate
        used.add((candidate, date))
        title_map[row["_content_id"]] = (candidate, date)
    return title_map


def load_zhihu_news(
    stored_rows: Optional[Dict[Tuple[str, datetime.date], Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Zhihu Q&A search results — one row per answer/article item.

    See :func:`_parse_zhihu_artifacts` / :func:`_finalize_zhihu_titles` for
    the cross-artifact dedup, the stored-row claim and the (title, date)
    disambiguation; votes carry the item's upvote count (VoteUpCount) or
    NULL when absent. *stored_rows* is the pre-parse DB state built by the
    build (see ``builds.text.__main__``).
    """
    item_records, _ = _parse_zhihu_artifacts()
    _finalize_zhihu_titles(item_records, stored_rows)
    for row in item_records:
        for key in _WORK_KEYS:
            row.pop(key, None)
    return item_records


def load_zhihu_comments(
    stored_rows: Optional[Dict[Tuple[str, datetime.date], Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Zhihu comments — one row per platform comment (root + embedded reply).

    Parent items are resolved through the finalized (title, date) pairs, so a
    comment follows its answer even when disambiguation renamed the title;
    *stored_rows* keeps those keys stable across runs (same claim as
    :func:`load_zhihu_news`). Rows whose parent item was dropped (no content
    etc.) are skipped, and in-batch PK duplicates (the same comment captured
    by two artifact copies, or one answer returned by several questions) are
    deduped here — a duplicated PK inside one upsert batch would raise
    "cannot affect row a second time". The DB-side PK check happens in
    builds.text.upsert (fetch_existing_comment_pks / filter_new_comments).
    """
    item_records, comment_records = _parse_zhihu_artifacts()
    title_map = _finalize_zhihu_titles(item_records, stored_rows)
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
