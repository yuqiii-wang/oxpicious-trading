/**
 * Feed data layer for the Market Sentiments by AI and News page: the AI /
 * News source toggle + keyword search + day-pick (±DATE_WINDOW_DAYS window)
 * + pagination, and the per-source calendar + items fetches. Everything
 * downstream (date strip, feed header, cards) reads through the returned
 * `active*` selectors / memos so the toggle only switches the source —
 * the scope, day pick, and search are shared.
 */
import { useEffect, useMemo, useState } from "react";
import type { DateEvent } from "@/shared/components/date-events/DateEventStrip";
import {
  fetchAiCalendar,
  fetchAiItems,
  fetchNewsCalendar,
  fetchNewsItems,
} from "@/lib/api-client";
import type {
  AiCalendarResponse,
  AiQaItemsResponse,
  NewsCalendarResponse,
  NewsItemsResponse,
} from "@shared/types";
import { DATE_WINDOW_DAYS, PAGE_SIZE, shiftDate } from "./constants";

export type FeedSource = "ai" | "news";

/** Shape passed in from the page: null defers the fetch (market rankings
 *  not resolved yet), {} = unscoped, else the industry-id set scope. */
type FeedScope = { industry_ids?: string[] } | null;

export function useSentimentFeed(args: {
  scope: FeedScope;
  refreshKey: number;
  /** Benchmark chart's visible window (chart↔strip sync) — takes
   *  precedence over the active calendar's bounds for the strip pins. */
  chartRange: { start: string; end: string } | null;
  /** The chart's full row span — bridges until the first range report. */
  chartSpan: { start: string; end: string } | null;
}) {
  const { scope, refreshKey, chartRange, chartSpan } = args;

  // ---- Feed source — "ai" (default, the LLM Q&A knowledge base) vs
  //      "news" (the raw news corpus). Both take the SAME scope + day
  //      pick; only the feed, the date strip, and the keyword search
  //      switch.
  const [feed, setFeed] = useState<FeedSource>("ai");

  // ---- Feed filters ---------------------------------------------------------
  /** Keyword filter over question OR answer (AI) / title OR content
   *  (news) — Enter submits. */
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState<string | null>(null);
  /** Picked day — set by clicking a benchmark-chart date OR a calendar dot
   *  (null = all days). The feed windows ±DATE_WINDOW_DAYS around the pick
   *  instead of narrowing to the single day. */
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  /** Inclusive [from, to] range around the pick (null = no day picked). */
  const dateWindow = useMemo(
    () =>
      selectedDate
        ? {
            from: shiftDate(selectedDate, -DATE_WINDOW_DAYS),
            to: shiftDate(selectedDate, DATE_WINDOW_DAYS),
          }
        : null,
    [selectedDate],
  );
  const [page, setPage] = useState(1);

  const scopeKey = scope == null ? null : JSON.stringify(scope);
  const scopeParams = useMemo(
    () => (scope == null ? null : { ...scope, search }),
    [scopeKey, search], // eslint-disable-line react-hooks/exhaustive-deps -- scopeKey is the canonical scope identity
  );

  // Scope/search/source changes reset the day pick + pagination.
  useEffect(() => {
    setSelectedDate(null);
    setPage(1);
  }, [scopeKey, search, feed]);

  const submitSearch = () => {
    setSearch(searchInput.trim() || null);
    setPage(1);
  };
  const clearSearch = () => {
    setSearchInput("");
    setSearch(null);
  };

  // ---- AI calendar (date-event strip dots) ---------------------------------
  const [calendar, setCalendar] = useState<AiCalendarResponse | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  useEffect(() => {
    if (scopeParams == null) return; // market mode: rankings not resolved yet
    let cancelled = false;
    setCalendarLoading(true);
    setCalendarError(null);
    fetchAiCalendar(scopeParams)
      .then((d) => {
        if (cancelled) return;
        setCalendar(d);
        setCalendarLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setCalendarError(e.message);
        setCalendarLoading(false);
      });
    return () => { cancelled = true; };
  }, [scopeParams, refreshKey]);

  // News-mode calendar — same shape (per-day counts + coverage bounds) for
  // the SAME scopeParams (industry_ids + search), fetched only while the
  // News source is active.
  const [newsCalendar, setNewsCalendar] = useState<NewsCalendarResponse | null>(null);
  const [newsCalendarLoading, setNewsCalendarLoading] = useState(false);
  const [newsCalendarError, setNewsCalendarError] = useState<string | null>(null);
  useEffect(() => {
    if (feed !== "news" || scopeParams == null) return; // market mode: rankings not resolved yet
    let cancelled = false;
    setNewsCalendarLoading(true);
    setNewsCalendarError(null);
    fetchNewsCalendar(scopeParams)
      .then((d) => {
        if (cancelled) return;
        setNewsCalendar(d);
        setNewsCalendarLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setNewsCalendarError(e.message);
        setNewsCalendarLoading(false);
      });
    return () => { cancelled = true; };
  }, [feed, scopeParams, refreshKey]);

  // Active calendar — the strip pins/dots read through this so the feed
  // toggle only switches the source.
  const activeCalendar = feed === "ai" ? calendar : newsCalendar;
  const activeCalendarLoading = feed === "ai" ? calendarLoading : newsCalendarLoading;
  const activeCalendarError = feed === "ai" ? calendarError : newsCalendarError;

  // The strip's axis pins: the benchmark chart's VISIBLE window (falling
  // back to the full span, then the active source's calendar, until the
  // first report lands / when the chart has no data). The strip renders no
  // slider of its own — the benchmark chart's slider IS the time control.
  const stripMin = chartRange?.start ?? chartSpan?.start ?? activeCalendar?.min_date ?? null;
  const stripMax = chartRange?.end ?? chartSpan?.end ?? activeCalendar?.max_date ?? null;

  // ---- AI items (one page) ---------------------------------------------------
  const [itemsData, setItemsData] = useState<AiQaItemsResponse | null>(null);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState<string | null>(null);
  useEffect(() => {
    if (scopeParams == null) {
      // Market mode: rankings not resolved yet — drop the stale page so the
      // feed never shows the other mode's items under the market label.
      setItemsData(null);
      return;
    }
    let cancelled = false;
    setItemsLoading(true);
    setItemsError(null);
    fetchAiItems({
      ...scopeParams,
      // A picked day windows the feed ±DATE_WINDOW_DAYS around itself
      // (date_from/date_to); no pick → unfiltered by date.
      ...(dateWindow
        ? { date_from: dateWindow.from, date_to: dateWindow.to }
        : { date: selectedDate }),
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    })
      .then((d) => {
        if (cancelled) return;
        setItemsData(d);
        setItemsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setItemsError(e.message);
        setItemsLoading(false);
      });
    return () => { cancelled = true; };
  }, [scopeParams, dateWindow, selectedDate, page, refreshKey]);

  // News-mode items — one page of articles for the SAME scope/day/page.
  const [newsItems, setNewsItems] = useState<NewsItemsResponse | null>(null);
  const [newsItemsLoading, setNewsItemsLoading] = useState(false);
  const [newsItemsError, setNewsItemsError] = useState<string | null>(null);
  useEffect(() => {
    if (feed !== "news") return;
    if (scopeParams == null) {
      // Market mode: rankings not resolved yet — drop the stale page so the
      // feed never shows the other mode's items under the market label.
      setNewsItems(null);
      return;
    }
    let cancelled = false;
    setNewsItemsLoading(true);
    setNewsItemsError(null);
    fetchNewsItems({
      ...scopeParams,
      // Same ±DATE_WINDOW_DAYS window around a pick as the AI feed.
      ...(dateWindow
        ? { date_from: dateWindow.from, date_to: dateWindow.to }
        : { date: selectedDate }),
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    })
      .then((d) => {
        if (cancelled) return;
        setNewsItems(d);
        setNewsItemsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setNewsItemsError(e.message);
        setNewsItemsLoading(false);
      });
    return () => { cancelled = true; };
  }, [feed, scopeParams, dateWindow, selectedDate, page, refreshKey]);

  // Active items — the feed header/empty-state read through this so the
  // feed toggle only switches the source. The per-source item lists are
  // exposed separately so the section's render branches stay typed.
  const activeItems = feed === "ai" ? itemsData : newsItems;
  const activeItemsLoading = feed === "ai" ? itemsLoading : newsItemsLoading;
  const activeItemsError = feed === "ai" ? itemsError : newsItemsError;

  // Date-strip events: one dot per day with rows for the ACTIVE source
  // (dot size ~ count).
  const stripEvents = useMemo<DateEvent[]>(
    () =>
      (activeCalendar?.days ?? []).map((d) => ({
        date: d.date,
        id: d.date, // the date string IS the selection id
        type: feed === "ai" ? "qa" : "news",
        title: feed === "ai" ? `${d.count} 条 AI 问答` : `${d.count} 条新闻`,
      })),
    [activeCalendar, feed],
  );

  const totalPages = Math.max(1, Math.ceil((activeItems?.total ?? 0) / PAGE_SIZE));

  return {
    feed,
    setFeed,
    searchInput,
    setSearchInput,
    search,
    submitSearch,
    clearSearch,
    selectedDate,
    setSelectedDate,
    dateWindow,
    page,
    setPage,
    activeCalendarLoading,
    activeCalendarError,
    stripEvents,
    stripMin,
    stripMax,
    activeItems,
    activeItemsLoading,
    activeItemsError,
    aiItems: itemsData?.items ?? null,
    newsItems: newsItems?.items ?? null,
    totalPages,
  };
}
