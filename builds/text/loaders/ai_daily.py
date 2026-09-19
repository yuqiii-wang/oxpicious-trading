"""builds.text.loaders.ai_daily — AI daily market summaries.

Parses the ``downloads.macro.ai_daily`` artifacts under
``temps/ai_daily/``: the general ``ai_daily_<date>.json`` envelopes (AI
answer + search references + embedded ``movers``) and the standalone
``*_movers.json`` envelopes.
"""
from __future__ import annotations

import datetime
import glob
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from builds.text import paths
from builds.text.loaders._parsing import parse_date_str

logger = logging.getLogger(__name__)

SOURCE = "ai_daily"

MOVERS_SUFFIX = "_movers.json"


def _ai_daily_file_date(path: str) -> Optional[datetime.date]:
    """The biz date embedded in an artifact name (ai_daily_YYYY-MM-DD[…])."""
    m = re.search(r"ai_daily_(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return datetime.date.fromisoformat(m.group(1)) if m else None


def hit_news_row(
    hit: Dict[str, Any],
    fallback_date: datetime.date,
) -> Optional[Dict[str, Any]]:
    """The text.news row for one search-reference hit.

    Shared by :func:`load_ai_daily` and builds.text.ai_daily_qa — the Q&A
    refs resolve to news_ids through the SAME (title, source, date) natural
    key this function writes, so every hit row written here is resolvable.
    Dated by publish_date_raw with the envelope's own date as fallback (the
    same shape the --store fast path wrote: source = media or 'web').
    """
    title = (hit.get("title") or "").strip()
    if not title:
        return None
    return {
        "title": title,
        "content": (hit.get("content") or "").strip() or None,
        "date": parse_date_str(hit.get("publish_date_raw")) or fallback_date,
        "source": (hit.get("media") or "").strip() or "web",
        "url": (hit.get("link") or "").strip() or None,
        "author": None,
    }


def iter_ai_daily_envelopes(
) -> List[Tuple[str, Dict[str, Any], bool, datetime.date]]:
    """Parse every artifact; return (path, data, is_movers_file, file_date).

    Standalone ``*_movers.json`` envelopes come FIRST, general artifacts
    after (both filename-sorted): the same question re-asked by a later run
    re-embeds a fresher copy that must win last-wins dedupe. *file_date* is
    the artifact's own data date — target_date, else the movers plan_date,
    else the filename's embedded date.
    """
    general_files = [
        p for p in sorted(glob.glob(
            os.path.join(paths.AI_DAILY_DIR, "ai_daily_*.json")))
        if not p.endswith(MOVERS_SUFFIX)]
    movers_files = sorted(glob.glob(os.path.join(
        paths.AI_DAILY_DIR, f"ai_daily_*{MOVERS_SUFFIX}")))

    envelopes: List[Tuple[str, Dict[str, Any], bool, datetime.date]] = []
    for json_path in movers_files + general_files:
        is_movers_file = json_path.endswith(MOVERS_SUFFIX)
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("    [ai_daily] unreadable %s: %s", json_path, e)
            continue
        if not isinstance(data, dict):
            continue
        file_date = (parse_date_str(data.get("target_date"))
                     or parse_date_str(data.get("plan_date"))
                     or _ai_daily_file_date(json_path))
        if file_date is None:
            logger.warning("    [ai_daily] skipped %s (no date)",
                           os.path.basename(json_path))
            continue
        envelopes.append((json_path, data, is_movers_file, file_date))
    return envelopes


def load_ai_daily() -> List[Dict[str, Any]]:
    """AI daily market summaries — answers, movers + search references.

    Three row kinds per envelope:

      * the general summary answer — one row titled by the asked question,
        dated by the artifact's target_date;
      * the industry-movers answers — one row per ``asked`` survivor,
        titled by its question, dated by the movers plan_date and pre-set
        with the mover's own industry_id (keywords.extract_for_articles
        keeps a pre-set industry_id over its text match);
      * every search reference hit of the general summary AND of each
        movers ask (:func:`hit_news_row`).
    """
    rows: List[Dict[str, Any]] = []
    n_ans = n_movers = n_refs = 0
    for json_path, data, is_movers_file, file_date in iter_ai_daily_envelopes():
        if not is_movers_file:
            question = (data.get("question") or "").strip()
            answer = (data.get("answer") or "").strip()
            if question and answer:
                rows.append({
                    "title": question,
                    "content": answer,
                    "date": file_date,
                    "source": SOURCE,
                    "url": None,
                    "author": None,
                })
                n_ans += 1
            for hit in data.get("references") or []:
                if not isinstance(hit, dict):
                    continue
                row = hit_news_row(hit, file_date)
                if row is not None:
                    rows.append(row)
                    n_refs += 1

        # Movers answers: the standalone file IS the envelope; the general
        # artifact embeds the same envelope under ``movers``.
        env = data if is_movers_file else data.get("movers")
        if not isinstance(env, dict):
            continue
        plan_date = parse_date_str(env.get("plan_date")) or file_date
        for asked in env.get("asked") or []:
            if not isinstance(asked, dict):
                continue
            question = (asked.get("question") or "").strip()
            answer = (asked.get("answer") or "").strip()
            if not question or not answer:
                continue
            rows.append({
                "title": question,
                "content": answer,
                "date": plan_date,
                "source": SOURCE,
                "url": None,
                "author": None,
                "industry_id":
                    (asked.get("industry_id") or "").strip() or None,
            })
            n_movers += 1
            # The movers ask's own search hits (embedded by
            # downloads.macro.ai_daily.movers since the --store removal —
            # older artifacts carry none).
            for hit in asked.get("references") or []:
                if not isinstance(hit, dict):
                    continue
                row = hit_news_row(hit, plan_date)
                if row is not None:
                    rows.append(row)
                    n_refs += 1

    logger.info("    [ai_daily] %d answers, %d movers, %d refs",
                n_ans, n_movers, n_refs)
    return rows
