"""llm_agents.online_search_summary.daily — the daily market request preset.

The one scheduled request the online-search summary agent serves every
biz day (driven by ``python -m downloads.macro.ai_daily``): a general
summary of the day's stock market — how the broad market traded and
which sectors saw significant rises and drops.

By default the request triggers on TODAY (Asia/Shanghai) when today is
a trading day, else on the LATEST biz date before today — a weekend /
holiday run still summarizes the most recent session instead of asking
about a non-trading day. The biz date is embedded in the query text and
the search publish-date window defaults to 5d (``RECENCY_5D`` — the
provider enum has no 5-day step, so ``oneWeek`` carries the 5-trading-
day window), matching the recency flow's 5-trading-day cooldown.

Pure domain: no network, no DB — the calendar lookup comes from
``_common._holidays_and_weekdays``.
"""
from __future__ import annotations

import datetime
from typing import Optional, Tuple

from _common._holidays_and_weekdays import (
    is_trading_day, last_business_day,
)

from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchOptions,
)

# text.llm_qa.category for the scheduled daily run (stored only with
# --store; the standalone artifact run skips the DB entirely).
DAILY_CATEGORY = "market_daily"

# Default reference count: a broad "which sectors moved" ask needs wider
# coverage than a single-newspoint question.
DAILY_COUNT = 15

# The recency flow's default publish-date window: 5d. The ZhiPu recency
# vocabulary has no 5-day step, so oneWeek (5 trading days) IS the 5d
# default — same horizon as the flow's cooldown.
RECENCY_5D = "oneWeek"


def default_daily_target(
    now: Optional[datetime.datetime] = None,
) -> datetime.date:
    """Today (Asia/Shanghai) when a trading day, else the latest biz date.

    This is the request's default trigger date: the most recent session
    a market summary can be about.
    """
    now = now or datetime.datetime.now(SHANGHAI_TZ)
    return last_business_day(now.date())


def daily_market_query(
    target: Optional[datetime.date] = None,
) -> str:
    """The daily question, with the biz date embedded.

    Asks for a general summary of the day's stock market and which
    sectors saw significant rises and drops. Passing *target* pins an
    explicit biz date (the ``ai_daily --date`` override); None anchors on
    the default trigger date (today / latest biz date).
    """
    d = target if target is not None else default_daily_target()
    return (f"{d.year}年{d.month}月{d.day}日股票市场整体行情如何？"
            f"当日哪些板块显著上涨，哪些板块明显下跌？")


def daily_recency(
    target: Optional[datetime.date] = None,
    today: Optional[datetime.date] = None,
) -> str:
    """The recency flow's default search publish-date window: 5d.

    Always ``RECENCY_5D`` (``oneWeek`` — the enum's 5-trading-day step);
    *target* / *today* are accepted for call-site compatibility but no
    longer shape the window. Dedupe over the same horizon is the flow's
    5-trading-day cooldown in ``downloads.macro.ai_daily.movers``.
    """
    return RECENCY_5D


def daily_request(
    target: Optional[datetime.date] = None,
    *,
    count: int = DAILY_COUNT,
) -> Tuple[str, SearchOptions]:
    """The complete daily request: (query, search options).

    The single entry point a scheduled driver needs — the query is the
    date-embedded market/sector question and the options carry the
    anchor-aware recency window and the wider default reference count.
    """
    d = target if target is not None else default_daily_target()
    query = daily_market_query(d)
    opts = SearchOptions(
        count=count, recency=daily_recency(d), content_size="high")
    opts.validate()
    return query, opts


def daily_target_or_clamp(
    target: datetime.date,
) -> Tuple[datetime.date, bool]:
    """Clamp a requested date onto the trading calendar.

    Returns (biz_date, clamped): a non-trading *target* (weekend /
    holiday) snaps back to the latest biz date on or before it, so an
    explicit ``--date`` never asks about a day with no session.
    """
    if is_trading_day(target):
        return target, False
    return last_business_day(target), True
