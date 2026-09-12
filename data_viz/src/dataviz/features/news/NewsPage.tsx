/**
 * News page — browse the text-schema news corpus (loaded by builds.text).
 *
 * Layout (built on the shared analysis nav kit):
 *   • SecNavShell — header (title + Refresh) + loading/error + page content.
 *     The sec_type toggle and code search are OFF: news is not a security
 *     type (the old header keyword bar moved into the question bar's 本地).
 *   • navSlot — source filter chips + the shared SecClassificationNav in its
 *     full TWO-COLUMN form (same as the security pages): LEFT sector →
 *     industry, RIGHT strategy → theme (BROAD/STRAT macro themes live here),
 *     mutually exclusive, no exchange row, no L3 security chips. Directly
 *     beneath the nav the QuestionSearchBar with TWO buttons sharing one
 *     input: 本地 naive-tokenizes the question into taxonomy keywords and
 *     drives the SAME keyword-search API as the old bar (search_mode=any,
 *     composed with the scope/source/author filters); 在线 runs the live
 *     zhihu question search (source + author travel with the request;
 *     non-enabled source chips disable while the bar has input). Below it
 *     the shared DateEventStrip (the PBoC OMA date-event component): one
 *     dot per date that has news, co-filtered by scope + keyword + source.
 *   • NewsFeedPage (shared) — social-media-style post feed matching
 *     (classification scope ∧ keyword ∧ source ∧ picked date); click a post
 *     to expand its full content and its threaded comments. Swapped for
 *     NewsSearchResults while an online search result is displayed.
 *
 * Co-filtering contract: the SAME scope params drive the date strip and the
 * item list, so picking an industry re-draws the dots (only dates with news
 * for that scope show one) and picking a date narrows the list; the keyword
 * and source apply to both (and to the nav tree counts themselves).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Chip, Stack, Typography } from "@mui/material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import DateEventStrip, { type DateEvent } from "@/shared/components/date-events/DateEventStrip";
import {
  fetchNewsAuthors,
  fetchNewsCalendar,
  fetchNewsItems,
  fetchNewsStrategyThemes,
  fetchNewsThemes,
  fetchNewsTokenize,
  invalidateCacheForPrefix,
  runNewsSearch,
} from "@/lib/api-client";
import type {
  NewsCalendarResponse,
  NewsDayCount,
  NewsItemsResponse,
  NewsSearchResponse,
} from "@shared/types";
import NewsFeedPage, { PAGE_SIZE } from "@/shared/components/news/NewsFeedPage";
import QuestionSearchBar, { QUESTION_SEARCH_ENABLED_SOURCES } from "./QuestionSearchBar";
import NewsSearchResults from "./NewsSearchResults";

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

  // ---- Question bar (beneath the classification nav): two search paths ----
  // • ONLINE — live zhihu content search; the page's source + author are sent
  //   with the request. Source chips outside QUESTION_SEARCH_ENABLED_SOURCES
  //   render disabled only while the Online button is hovered or an online
  //   search is running / its results are shown — TYPING does not touch them
  //   (an unsupported source resolves to the first enabled one at submit).
  // • LOCAL — the ORIGINAL keyword search: the question is naive-tokenized
  //   into taxonomy keywords (WSL python), and the token list drives the
  //   same search param as the old header bar — composed with the active
  //   scope/source/author filters on every fetch below (search_mode=any:
  //   OR across tokens).
  const [questionInput, setQuestionInput] = useState("");
  const [searchRunning, setSearchRunning] = useState(false);
  const [searchResult, setSearchResult] = useState<NewsSearchResponse | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [onlineHover, setOnlineHover] = useState(false);
  // Online-search active: a run is in flight or its results are displayed.
  const onlineActive = searchRunning || searchResult !== null;

  // Local-search state: searchTerm/searchMode feed scopeParams (calendar +
  // authors + items); localTokens only labels the bar's active chip.
  const [searchTerm, setSearchTerm] = useState<string | null>(null);
  const [searchMode, setSearchMode] = useState<"any" | "all">("any");
  const [localRunning, setLocalRunning] = useState(false);
  const [localEmpty, setLocalEmpty] = useState(false);

  const runOnlineSearch = useCallback(async () => {
    const question = questionInput.trim();
    if (!question || searchRunning) return;
    setSearchRunning(true);
    setSearchError(null);
    // "All" (null) resolves to the first enabled source for the request.
    const searchSource =
      source && QUESTION_SEARCH_ENABLED_SOURCES.includes(source)
        ? source
        : QUESTION_SEARCH_ENABLED_SOURCES[0];
    const resp = await runNewsSearch({ question, source: searchSource, author });
    setSearchRunning(false);
    setSearchResult(resp);
    if (!resp.success && !resp.already_running) {
      setSearchError(resp.stderr_tail ?? "未知错误");
    }
  }, [questionInput, searchRunning, source, author]);

  const runLocalSearch = useCallback(async () => {
    const question = questionInput.trim();
    if (!question || localRunning) return;
    setLocalRunning(true);
    setLocalEmpty(false);
    const { tokens, error } = await fetchNewsTokenize(question);
    setLocalRunning(false);
    if (error || tokens.length === 0) {
      setLocalEmpty(true);
      return;
    }
    setSearchMode("any");
    setSearchTerm(tokens.join(" "));
  }, [questionInput, localRunning]);

  const clearLocalSearch = useCallback(() => {
    setSearchTerm(null);
    setSearchMode("any"); // moot while searchTerm is null
    setLocalEmpty(false);
  }, []);

  const clearQuestionSearch = useCallback(() => {
    setQuestionInput("");
    setSearchResult(null);
    setSearchError(null);
    setSearchTerm(null);
    setLocalEmpty(false);
  }, []);

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

  // Selected date + pagination for the corpus list (local search state lives
  // with the question-bar block above).
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
    () => ({ ...scope, search: searchTerm, search_mode: searchTerm ? searchMode : null, source, author }),
    [scopeKey, searchTerm, searchMode, source, author], // eslint-disable-line react-hooks/exhaustive-deps -- scopeKey is the canonical scope identity
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
    fetchNewsAuthors({
      ...scope,
      search: searchTerm,
      search_mode: searchTerm ? searchMode : null,
      source,
    })
      .then((a) => {
        if (!cancelled) setAuthors(a);
      })
      .catch(() => {
        if (!cancelled) setAuthors([]);
      });
    return () => { cancelled = true; };
  }, [scopeKey, searchTerm, searchMode, source]); // eslint-disable-line react-hooks/exhaustive-deps

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
      // Replace the shell's default security nav with the news variant:
      // full two-column classification (sector/industry vs strategy/theme —
      // mutually exclusive, no exchange row, no L3 chips) + question search
      // bar + source chips + the shared date-event strip underneath.
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
          {/* Question search bar — directly beneath the classification nav.
              本地 tokenizes the question and drives the keyword search API
              (with the scope/source/author filters below); 在线 runs the
              live source search (zhihu for now). The old header keyword bar
              is merged into this bar's 本地 button. */}
          <QuestionSearchBar
            value={questionInput}
            onChange={setQuestionInput}
            onLocalSearch={runLocalSearch}
            onOnlineSearch={runOnlineSearch}
            onClearLocal={clearLocalSearch}
            onClear={clearQuestionSearch}
            onOnlineHoverChange={setOnlineHover}
            localRunning={localRunning}
            onlineRunning={searchRunning}
            localTerms={searchTerm}
            localEmpty={localEmpty}
            active={searchResult !== null}
          />
          {/* Source + Author rows — directly below the Industry row, same
              chip style as the classification rows. Both narrow the nav
              counts, the date strip and the list — and travel with the
              question search request. Sources without question-search
              support render disabled only while the Online button is
              hovered or an online search is active — never on typing. */}
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
                disabled={(onlineHover || onlineActive) && !QUESTION_SEARCH_ENABLED_SOURCES.includes(s)}
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
      {searchResult ? (
        // Live question-search results (raw zhihu items) replace the feed
        // until the search is cleared. The date strip + nav above keep their
        // corpus state untouched.
        <NewsSearchResults
          result={searchResult}
          error={searchError}
          onDismiss={clearQuestionSearch}
        />
      ) : (
        <NewsFeedPage
          items={itemsData?.items ?? []}
          total={itemsData?.total ?? 0}
          page={page}
          onPageChange={setPage}
          loading={itemsLoading}
          error={itemsError}
          scopeLabel={`${scopeLabel}${selectedDate ? ` · ${selectedDate}` : ""}${searchTerm ? ` · “${searchTerm}”` : ""}${source ? ` · ${source}` : ""}${author ? ` · ${author}` : ""}`}
        />
      )}
    </SecNavShell>
  );
}
