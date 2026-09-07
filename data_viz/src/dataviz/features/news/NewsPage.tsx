/**
 * News page — browse the text-schema news corpus (loaded by builds.text).
 *
 * Layout (built on the shared analysis nav kit):
 *   • SecNavShell — header (title + keyword CodeSearchBar + Refresh) +
 *     loading/error + page content. The sec_type toggle and code search are
 *     OFF: news is not a security type.
 *   • navSlot — source filter chips + the shared SecClassificationNav in its
 *     full TWO-COLUMN form (same as the security pages): LEFT sector →
 *     industry, RIGHT strategy → theme (BROAD/STRAT macro themes live here),
 *     mutually exclusive, no exchange row, no L3 security chips. Below it the
 *     shared DateEventStrip (the PBoC OMA date-event component): one dot per
 *     date that has news, co-filtered by scope + keyword + source.
 *   • NewsList — articles matching (classification scope ∧ keyword ∧ source
 *     ∧ picked date).
 *
 * Co-filtering contract: the SAME scope params drive the date strip and the
 * item list, so picking an industry re-draws the dots (only dates with news
 * for that scope show one) and picking a date narrows the list; the keyword
 * and source apply to both (and to the nav tree counts themselves).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Chip, Stack, Typography } from "@mui/material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import DateEventStrip, { type DateEvent } from "@/shared/components/date-events/DateEventStrip";
import CodeSearchBar from "@/components/CodeSearchBar";
import {
  fetchNewsAuthors,
  fetchNewsCalendar,
  fetchNewsItems,
  fetchNewsStrategyThemes,
  fetchNewsThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { NewsCalendarResponse, NewsDayCount, NewsItemsResponse } from "@shared/types";
import NewsList, { PAGE_SIZE } from "./NewsList";

/** Sources produced by downloads.macro.* (mirrors builds/text/loaders.py). */
const NEWS_SOURCES = ["gov", "ndrc", "pboc_lpr", "pboc_omo", "pboc_oma", "zhihu"] as const;

/** Default scope per product spec: zhihu answers for the 上证 broad theme.
 *  Applied once when the strategy tree first loads (user can change it). */
const DEFAULT_SOURCE = "zhihu";
const DEFAULT_STRATEGY_ID = "BROAD";
const DEFAULT_THEME_SLUG = "broad_sse";

export default function NewsPage() {
  // Source / author filters — narrow the nav counts, the date strip AND the
  // list. Both chip rows render BELOW the Industry row in the nav.
  const [source, setSource] = useState<string | null>(DEFAULT_SOURCE);
  const [author, setAuthor] = useState<string | null>(null);

  // Nav trees: both columns, counts scoped to the active source + author.
  // themesSources is rebuilt per render so a source/author change is picked
  // up by the reload that nav.refresh() below triggers.
  const themesSources = useMemo(
    () => ({
      index: {
        themes: () => fetchNewsThemes(source, author),
        strategyThemes: () => fetchNewsStrategyThemes(source, author),
      },
    }),
    [source, author],
  );

  const nav = useSecNav({
    themesSources,
    defaultSecType: "index",
    dataLabel: "news",
    onInvalidateCache: () => invalidateCacheForPrefix("/api/news/"),
  });

  // Source change → reload the trees (and, via refreshKey, strip + list).
  // Skipped on mount — the hook's initial load already covers the default.
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    nav.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, author]);

  // Default scope (zhihu / 宽基 / 上证): applied ONCE when the strategy tree
  // first loads, so the page opens on the same view the product defaults to.
  // Only fills an untouched selection — changing filters afterwards is free.
  const defaultAppliedRef = useRef(false);
  useEffect(() => {
    if (defaultAppliedRef.current) return;
    if (nav.strategies.length === 0) return;
    if (nav.strategyId || nav.themeSlug) {
      defaultAppliedRef.current = true; // user (or a restore) picked already
      return;
    }
    const strategy = nav.strategies.find((s) => s.sector_id === DEFAULT_STRATEGY_ID);
    const theme = strategy?.industries.find((i) => i.industry_slug === DEFAULT_THEME_SLUG);
    if (!strategy || !theme) {
      defaultAppliedRef.current = true; // tree shape changed — don't retry
      return;
    }
    defaultAppliedRef.current = true;
    nav.handleStrategyChange(DEFAULT_STRATEGY_ID);
    nav.handleThemeChange(DEFAULT_THEME_SLUG);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.strategies]);

  // Keyword search (free text, whitespace = AND) — page-local state wired to
  // a CodeSearchBar in the shell header (the nav kit's code search is off).
  const [searchTerm, setSearchTerm] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  // Classification scope across BOTH columns: L2 chip → its canonical
  // industry_id; L1-only → the whole sector (the API expands sector_id → its
  // industry_ids via the catalog); none → unscoped.
  const slugToIndustryId = useMemo(() => {
    const m = new Map<string, string>();
    for (const tree of [nav.sectors, nav.strategies]) {
      for (const s of tree) {
        for (const ind of s.industries) m.set(ind.industry_slug, ind.industry_id);
      }
    }
    return m;
  }, [nav.sectors, nav.strategies]);
  const scope = useMemo(() => {
    if (nav.industrySlug || nav.themeSlug) {
      const industryId =
        slugToIndustryId.get(nav.industrySlug ?? "") ??
        slugToIndustryId.get(nav.themeSlug ?? "") ??
        null;
      if (industryId) return { industry_id: industryId };
    }
    if (nav.sectorId) return { sector_id: nav.sectorId };
    if (nav.strategyId) return { sector_id: nav.strategyId };
    return {};
  }, [nav.industrySlug, nav.themeSlug, nav.sectorId, nav.strategyId, slugToIndustryId]);
  const scopeKey = JSON.stringify(scope);
  const scopeParams = useMemo(
    () => ({ ...scope, search: searchTerm, source, author }),
    [scopeKey, searchTerm, source, author], // eslint-disable-line react-hooks/exhaustive-deps -- scopeKey is the canonical scope identity
  );

  // Scope/search/source changes reset the day pick + pagination + author pick
  // (the author list itself is scoped by everything except author).
  useEffect(() => {
    setSelectedDate(null);
    setPage(1);
    setAuthor(null);
  }, [scopeKey, searchTerm, source]);

  // Top authors for the filter chips (scoped by everything except author).
  const [authors, setAuthors] = useState<Array<{ author: string; count: number }>>([]);
  useEffect(() => {
    let cancelled = false;
    fetchNewsAuthors({ ...scope, search: searchTerm, source })
      .then((a) => {
        if (!cancelled) setAuthors(a);
      })
      .catch(() => {
        if (!cancelled) setAuthors([]);
      });
    return () => { cancelled = true; };
  }, [scopeKey, searchTerm, source]); // eslint-disable-line react-hooks/exhaustive-deps

  const [calendar, setCalendar] = useState<NewsCalendarResponse | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setCalendarLoading(true);
    setCalendarError(null);
    fetchNewsCalendar(scopeParams)
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
  }, [scopeParams, nav.refreshKey]);

  const [itemsData, setItemsData] = useState<NewsItemsResponse | null>(null);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setItemsLoading(true);
    setItemsError(null);
    fetchNewsItems({
      ...scopeParams,
      date: selectedDate,
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
  }, [scopeParams, selectedDate, page, nav.refreshKey]);

  // Date-strip events: one dot per date with news (dot size ~ count).
  const stripEvents = useMemo<DateEvent[]>(
    () =>
      (calendar?.days ?? []).map((d: NewsDayCount) => ({
        date: d.date,
        id: d.date, // the date string IS the selection id
        type: "news",
        title: `${d.count} 篇新闻`,
      })),
    [calendar],
  );

  // headerLabel already appends "(All)" for sector-only / strategy-only picks.
  const scopeLabel =
    nav.industrySlug || nav.themeSlug || nav.sectorId || nav.strategyId
      ? nav.headerLabel
      : "全部行业";

  return (
    <SecNavShell
      nav={nav}
      title="News"
      backPath="/dataviz"
      subtitle={`${scopeLabel}${selectedDate ? ` · ${selectedDate}` : " · 全部日期"} — 点选日期圆点筛选当日新闻`}
      showSearch={false}
      headerExtra={
        <CodeSearchBar
          activeCode={searchTerm}
          onSearch={(kw) => setSearchTerm(kw)}
          onClear={() => setSearchTerm(null)}
          placeholder="关键词搜索（空格 = AND）"
          activeLabel="关键词"
        />
      }
      // Replace the shell's default security nav with the news variant:
      // full two-column classification (sector/industry vs strategy/theme —
      // mutually exclusive, no exchange row, no L3 chips) + source chips +
      // the shared date-event strip underneath.
      navSlot={
        <Box sx={{ mb: 1.5 }}>
          <SecClassificationNav
            sectors={nav.sectors}
            sectorId={nav.sectorId}
            industrySlug={nav.industrySlug}
            onSectorChange={nav.handleSectorChange}
            onIndustryChange={nav.handleIndustryChange}
            onExchangeChange={nav.handleExchangeChange}
            strategies={nav.strategies}
            strategyId={nav.strategyId}
            themeSlug={nav.themeSlug}
            onStrategyChange={nav.handleStrategyChange}
            onThemeChange={nav.handleThemeChange}
            exchange={null}
            showExchange={false}
            loading={nav.loading}
            sx={{ mb: 1 }}
          />
          {/* Source + Author rows — directly below the Industry row, same
              chip style as the classification rows. Both narrow the nav
              counts, the date strip and the list. */}
          <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mb: 0.75 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}>
              Source
            </Typography>
            <Chip
              label="All"
              size="small"
              color={source === null ? "primary" : "default"}
              variant={source === null ? "filled" : "outlined"}
              onClick={() => setSource(null)}
              sx={{ fontSize: "0.7rem" }}
            />
            {NEWS_SOURCES.map((s) => (
              <Chip
                key={s}
                label={s}
                size="small"
                color={source === s ? "primary" : "default"}
                variant={source === s ? "filled" : "outlined"}
                onClick={() => setSource(source === s ? null : s)}
                sx={{ fontSize: "0.7rem" }}
              />
            ))}
          </Stack>
          {authors.length > 0 && (
            <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mb: 0.75 }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}>
                Author
              </Typography>
              <Chip
                label="All"
                size="small"
                color={author === null ? "primary" : "default"}
                variant={author === null ? "filled" : "outlined"}
                onClick={() => setAuthor(null)}
                sx={{ fontSize: "0.7rem" }}
              />
              {authors.map((a) => (
                <Chip
                  key={a.author}
                  label={`${a.author} (${a.count})`}
                  size="small"
                  color={author === a.author ? "primary" : "default"}
                  variant={author === a.author ? "filled" : "outlined"}
                  onClick={() => setAuthor(author === a.author ? null : a.author)}
                  sx={{
                    fontSize: "0.7rem",
                    "& .MuiChip-label": {
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      maxWidth: 220,
                    },
                  }}
                />
              ))}
            </Stack>
          )}
          <DateEventStrip
            events={stripEvents}
            typeMeta={{ news: { label: "新闻", color: "#5470c6" } }}
            minDate={calendar?.min_date ?? null}
            maxDate={calendar?.max_date ?? null}
            selectedId={selectedDate}
            onEventClick={(e) =>
              setSelectedDate((cur) => (String(e.id) === String(cur) ? null : String(e.id)))
            }
            height={72}
            showLegend={false}
            emptyText={calendarLoading ? "加载中…" : "无新闻记录"}
            enableZoom
            defaultWindowDays={92}
          />
          {calendarError && (
            <Box component="pre" sx={{ color: "error.main", fontSize: "0.75rem", whiteSpace: "pre-wrap" }}>
              Failed to load calendar: {calendarError}
            </Box>
          )}
        </Box>
      }
    >
      <NewsList
        items={itemsData?.items ?? []}
        total={itemsData?.total ?? 0}
        page={page}
        onPageChange={setPage}
        loading={itemsLoading}
        error={itemsError}
        scopeLabel={`${scopeLabel}${selectedDate ? ` · ${selectedDate}` : ""}${searchTerm ? ` · “${searchTerm}”` : ""}${source ? ` · ${source}` : ""}${author ? ` · ${author}` : ""}`}
      />
    </SecNavShell>
  );
}
