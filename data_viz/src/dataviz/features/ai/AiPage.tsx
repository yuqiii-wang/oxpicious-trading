/**
 * AI page (DataViz > AI tab) — /dataviz/ai.
 *
 * Browses the LLM Q&A knowledge base (text.llm_qa / text.llm_qa_refs /
 * text.news_digestions, written by llm_agents) through the same nav kit as
 * the other nav pages — it sits next to the News page, whose date-event
 * strip and feed idioms it reuses. Layout, top to bottom:
 *
 *   1. Header — Index / Stock sec_type toggle (CodeSearchBar + Refresh):
 *      the toggle switches BOTH nav trees and the code trend's data source.
 *   2. SecClassificationNav — the security classification (L1 sector → L2
 *      industry + parallel strategy → theme + exchange + L3 security chips),
 *      from the baseline registries per sec_type (same sources as Recent
 *      Movements). EVERY pick scopes the date strip + Q&A feed by industry:
 *      the L2 chip by its industry_id; the strategy/theme pick by the UNION
 *      of its member securities' industry_ids; the L3 chip / code search by
 *      the security's OWN industry_id. A pick with no belonged industry
 *      (broad-market indexes are strategy-only) empties the feed — it never
 *      falls back to "show all".
 *   3. Code trend — once a security is picked, the shared CodeTrendChart
 *      renders its daily OHLC trend; the header Expand/Shrink toggle folds
 *      the panel between a compact strip and a tall chart, and the close
 *      button drops the pick.
 *   4. DateEventStrip — the shared date-event bar (imported from the News
 *      page's kit): one dot per TRADING day that has Q&A rows (the backend
 *      rolls ask-question holidays/weekends back to the previous business
 *      day, e.g. 2026-09-12 → 2026-09-11), co-filtered by the
 *      classification scope + keyword + status; clicking a dot narrows the
 *      feed to that day and jumps the code trend's slider to it. While the
 *      trend is live the strip has NO slider of its own — its VISIBLE
 *      window IS the code trend's visible window (same start/end/length;
 *      dragging the trend's slider re-windows the dots in lockstep). With
 *      no code picked the strip's own time slider comes back over its
 *      calendar range.
 *   5. Q&A feed — the LLM Q&A content items (AiQaCard post cards: question,
 *      answer snippet; click expands the full answer + context + per-ref
 *      citation list with the average digestion sentiment).
 *
 * Nav trees sources per sec_type (baseline registries — no ETF on this
 * page, the toggle is Index vs Stock only):
 *   Index → /api/index-baseline/themes + /api/index-baseline/strategy-themes
 *   Stock → /api/stock-baseline/themes + /api/stock-baseline/strategy-themes
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  CircularProgress,
  IconButton,
  InputAdornment,
  Pagination,
  Stack,
  TextField,
  ToggleButton,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Close as CloseIcon,
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  Search as SearchIcon,
} from "@mui/icons-material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavThemesSource } from "@/shared/components/sec-nav";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import DateEventStrip, { type DateEvent } from "@/shared/components/date-events/DateEventStrip";
import CodeTrendChart from "@/components/CodeTrendChart";
import AiQaCard from "./AiQaCard";
import {
  fetchAiCalendar,
  fetchAiItems,
  fetchIndexStrategyThemes,
  fetchIndexThemes,
  fetchStockStrategyThemes,
  fetchStockThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { AiCalendarResponse, AiQaItemsResponse } from "@shared/types";

/** Nav trees endpoints per sec_type (baseline classification registries). */
const THEMES_SOURCES: Partial<Record<"index" | "stock", SecNavThemesSource>> = {
  index: {
    themes: (exchange) => fetchIndexThemes(exchange),
    strategyThemes: (exchange) => fetchIndexStrategyThemes(exchange),
  },
  stock: {
    themes: (exchange) => fetchStockThemes(exchange),
    strategyThemes: (exchange) => fetchStockStrategyThemes(exchange),
  },
};

/** Cache prefixes invalidated on Refresh, per sec_type. */
const CACHE_PREFIXES: Record<"index" | "stock", string[]> = {
  index: ["/api/index-baseline/themes", "/api/index-baseline/strategy-themes"],
  stock: ["/api/stock-baseline/themes", "/api/stock-baseline/strategy-themes"],
};

/** Feed page size (server caps at 100). */
const PAGE_SIZE = 20;

/** Suffix-insensitive code match (000001.SZ ≙ 000001) — same as CodeSearchBar. */
function normCode(code: string): string {
  return code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
}

/** Code-trend panel heights: compact strip vs expanded chart. */
const TREND_HEIGHT_COLLAPSED = 300;
const TREND_HEIGHT_EXPANDED = 640;

export default function AiPage() {
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    defaultSecType: "index",
    dataLabel: "classification",
    onInvalidateCache: () => {
      for (const prefixes of Object.values(CACHE_PREFIXES)) {
        for (const p of prefixes) invalidateCacheForPrefix(p);
      }
    },
  });

  // ---- Q&A feed filters ----------------------------------------------------
  /** Keyword filter over question OR answer (Enter submits). */
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState<string | null>(null);
  /** Picked day from the date-event strip (null = all days). */
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  // Code-trend expand/shrink (the panel starts compact).
  const [trendExpanded, setTrendExpanded] = useState(false);

  // ---- Trend ↔ date-strip sync ---------------------------------------------
  // The strip has NO slider of its own and — while the trend is live — its
  // VISIBLE date window IS the code trend's visible window (same start,
  // end, and length): StockOhlcChart reports its visible [start, end] via
  // onVisibleRangeChange and the strip pins its axis to exactly that range,
  // so dragging the trend's slider re-windows the dots in lockstep. (The
  // full row span — trendSpan from onLoaded — only bridges the moment
  // before the first range report lands.) Sync runs BOTH ways: clicking a
  // dot sends focusDateRequest, the trend's dataZoom window jumps to the
  // event date, and the strip re-windows via the report above. Events
  // outside the trend's window are off-scale while zoomed away — the jump
  // brings them back into view.
  // Without a picked code there is no trend and no jump — the strip falls
  // back to its own calendar range and gets its own time slider.
  const [trendRange, setTrendRange] = useState<{ start: string; end: string } | null>(null);
  const [trendSpan, setTrendSpan] = useState<{ start: string; end: string } | null>(null);
  const [focusReq, setFocusReq] = useState<{ date: string; seq: number } | null>(null);
  const handleTrendRange = useCallback(
    (r: { start: string; end: string } | null) => setTrendRange(r),
    [],
  );
  // Fired after each trend load settles: the FULL row span bridges the
  // gap until the first visible-range report arrives (null for an empty
  // load).
  const handleTrendLoaded = useCallback(
    (data: { rows: Array<{ date: string }> } | null) => {
      const rows = data?.rows ?? [];
      setTrendSpan(rows.length > 0
        ? { start: rows[0].date, end: rows[rows.length - 1].date }
        : null);
    },
    [],
  );
  const trendLive = nav.searchCode != null;
  useEffect(() => {
    if (!trendLive) {
      setTrendRange(null);
      setTrendSpan(null);
      setFocusReq(null);
    }
  }, [trendLive]);
  // Identity-stable chartOptions (a fresh object per render would churn the
  // chart subtree — same idiom as RecentMovements' triggerChartOptions).
  const trendChartOptions = useMemo(
    () => ({ onVisibleRangeChange: handleTrendRange, focusDateRequest: focusReq }),
    [handleTrendRange, focusReq],
  );

  // Q&A feed scope — text.llm_qa.industry_id carries industry tags (SEMI,
  // BANKS, …), so the feed is ALWAYS scoped by industry, resolved from the
  // active nav pick in this order:
  //   1. A picked security (L3 chip / code search / code trend pick): its OWN
  //      industry_id from the LEFT industry tree — whichever column the pick
  //      came through (same suffix-insensitive match as CodeSearchBar).
  //      Broad-market indices (000001 上证指数 → BROAD_SSE) live only in the
  //      RIGHT strategy tree, so a LEFT miss falls back to the strategy
  //      themes' own industry_id — the same tag the recency flow
  //      (downloads.macro.ai_daily) stores the broad-market daily QA under.
  //   2. A RIGHT strategy/theme pick: the UNION of the themes' own
  //      industry_ids and their member securities' industry_ids (theme pick
  //      → that theme; strategy-only pick → every theme under it).
  //   3. An L2 industry chip → its canonical industry_id; an L1-only pick →
  //      the whole sector (the API expands sector_id → industry_ids).
  // A pick that resolves to NO industries scopes to an EMPTY set and the
  // feed goes empty — a nav pick never silently falls back to "show all".
  // Unscoped ({}) only when nothing is picked at all.
  const slugToIndustryId = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) m.set(ind.industry_slug, ind.industry_id);
    }
    return m;
  }, [nav.sectors]);
  const codeToIndustry = useMemo(() => {
    const m = new Map<string, { industryId: string; label: string }>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) {
        for (const it of ind.items) {
          m.set(normCode(it.code), {
            industryId: ind.industry_id,
            label: `${s.sector_label} / ${ind.industry_label}`,
          });
        }
      }
    }
    return m;
  }, [nav.sectors]);
  // RIGHT strategy-tree fallback: broad-market families carry real
  // industry_ids (宽基 → BROAD_SSE …), so their members resolve too.
  const strategyCodeToIndustry = useMemo(() => {
    const m = new Map<string, { industryId: string; label: string }>();
    for (const s of nav.strategies) {
      for (const ind of s.industries) {
        for (const it of ind.items) {
          if (!m.has(normCode(it.code))) {
            m.set(normCode(it.code), {
              industryId: ind.industry_id,
              label: `${s.sector_label} / ${ind.industry_label}`,
            });
          }
        }
      }
    }
    return m;
  }, [nav.strategies]);
  const scope = useMemo(() => {
    if (nav.searchCode) {
      const code = normCode(nav.searchCode);
      const hit = codeToIndustry.get(code) ?? strategyCodeToIndustry.get(code);
      return hit ? { industry_id: hit.industryId } : { industry_ids: [] };
    }
    if (nav.strategyId || nav.themeSlug) {
      const themes = (nav.strategies.find((s) => s.sector_id === nav.strategyId)
        ?.industries ?? []).filter(
        (t) => !nav.themeSlug || t.industry_slug === nav.themeSlug,
      );
      const ids = new Set<string>();
      for (const t of themes) {
        ids.add(t.industry_id);
        for (const it of t.items) {
          const hit = codeToIndustry.get(normCode(it.code));
          if (hit) ids.add(hit.industryId);
        }
      }
      return { industry_ids: [...ids] };
    }
    if (nav.industrySlug) {
      const industryId = slugToIndustryId.get(nav.industrySlug) ?? null;
      if (industryId) return { industry_id: industryId };
    }
    if (nav.sectorId) return { sector_id: nav.sectorId };
    return {};
  }, [
    nav.searchCode, nav.strategyId, nav.themeSlug, nav.strategies,
    nav.industrySlug, nav.sectorId, codeToIndustry, slugToIndustryId,
    strategyCodeToIndustry,
  ]);
  const scopeKey = JSON.stringify(scope);
  const scopeParams = useMemo(
    () => ({ ...scope, search }),
    [scopeKey, search], // eslint-disable-line react-hooks/exhaustive-deps -- scopeKey is the canonical scope identity
  );

  // Scope/search changes reset the day pick + pagination.
  useEffect(() => {
    setSelectedDate(null);
    setPage(1);
  }, [scopeKey, search]);

  // ---- Calendar (date-event strip dots) ------------------------------------
  const [calendar, setCalendar] = useState<AiCalendarResponse | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  useEffect(() => {
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
  }, [scopeParams, nav.refreshKey]);

  // The strip's axis pins: the trend's VISIBLE window while live (falling
  // back to the full span, then the calendar, until the first report
  // lands), else the calendar range for the strip's own slider. (Declared
  // after the calendar state — it reads it.)
  const stripMin = trendLive
    ? trendRange?.start ?? trendSpan?.start ?? calendar?.min_date ?? null
    : calendar?.min_date ?? null;
  const stripMax = trendLive
    ? trendRange?.end ?? trendSpan?.end ?? calendar?.max_date ?? null
    : calendar?.max_date ?? null;

  // ---- Q&A items (one page) -------------------------------------------------
  const [itemsData, setItemsData] = useState<AiQaItemsResponse | null>(null);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setItemsLoading(true);
    setItemsError(null);
    fetchAiItems({
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

  // Date-strip events: one dot per day with Q&A rows (dot size ~ count).
  const stripEvents = useMemo<DateEvent[]>(
    () =>
      (calendar?.days ?? []).map((d) => ({
        date: d.date,
        id: d.date, // the date string IS the selection id
        type: "qa",
        title: `${d.count} 条 AI 问答`,
      })),
    [calendar],
  );

  // Feed scope label — mirrors the scope mapping above: the picked
  // security's industry wins (an industry-less security — e.g. a broad-market
  // index — shows its own name), then a strategy/theme pick's label (the
  // feed is scoped by its members' industries), then the LEFT-column picks.
  const scopeLabel = useMemo(() => {
    if (nav.searchCode) {
      const hit = codeToIndustry.get(normCode(nav.searchCode));
      if (hit) return hit.label;
      return nav.findItemName(nav.searchCode) ?? nav.searchCode;
    }
    if (nav.industrySlug || nav.sectorId) return nav.headerLabel;
    if (nav.strategyId || nav.themeSlug) return nav.headerLabel;
    return "全部行业";
  }, [
    nav.searchCode, nav.strategyId, nav.themeSlug,
    nav.industrySlug, nav.sectorId, nav.headerLabel, codeToIndustry, nav.findItemName,
  ]);
  const totalPages = Math.max(1, Math.ceil((itemsData?.total ?? 0) / PAGE_SIZE));

  return (
    <SecNavShell
      nav={nav}
      title="AI"
      backPath="/dataviz"
      subtitle={`${scopeLabel}${selectedDate ? ` · ${selectedDate}` : " · 全部日期"} — LLM 问答知识库（text.llm_qa）· 点选日期圆点筛选当日问答`}
      secTypes={["index", "stock"]}
      refreshTooltip="Refresh the classification trees (bypass cache)"
      errorPrefix="classification data"
      navSlot={
        <Box sx={{ mb: 1.5 }}>
          <SecClassificationNav
            sectors={nav.sectors}
            sectorId={nav.sectorId}
            industrySlug={nav.industrySlug}
            exchange={nav.exchange}
            onSectorChange={nav.handleSectorChange}
            onIndustryChange={nav.handleIndustryChange}
            onExchangeChange={nav.handleExchangeChange}
            strategies={nav.strategies}
            strategyId={nav.strategyId}
            themeSlug={nav.themeSlug}
            onStrategyChange={nav.handleStrategyChange}
            onThemeChange={nav.handleThemeChange}
            itemKind={nav.secType === "index" ? "Index" : "Stock"}
            selectedItemCode={nav.searchCode}
            onItemSelected={nav.onItemSelected}
            onClearItemSelection={nav.onClearItemSelection}
            loading={nav.loading}
            sx={{ mb: 1 }}
          />

          {/* ---- Keyword row — directly below the Industry row, same
                  row idiom as the News page's filter rows. Narrows the date
                  strip and the Q&A feed. ---- */}
          <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mb: 0.75, alignItems: "center" }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}>
              Search
            </Typography>
            <TextField
              size="small"
              placeholder="搜索问答（Enter）"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setSearch(searchInput.trim() || null);
                  setPage(1);
                }
              }}
              InputProps={{
                sx: { fontSize: "0.75rem", height: 26 },
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ fontSize: 15, color: "text.secondary" }} />
                  </InputAdornment>
                ),
                endAdornment: search ? (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      aria-label="清除搜索"
                      onClick={() => { setSearchInput(""); setSearch(null); }}
                      sx={{ p: 0.25 }}
                    >
                      <CloseIcon sx={{ fontSize: 13 }} />
                    </IconButton>
                  </InputAdornment>
                ) : undefined,
              }}
              sx={{ ml: "auto", width: 220, "& .MuiOutlinedInput-root": { borderRadius: 5 } }}
            />
          </Stack>

          {/* ---- Code trend (expandable / shrink) — once a security is
                  picked via code search or an L3 chip. The header toggle
                  folds the panel between compact and tall; the close button
                  drops the pick. ---- */}
          {nav.searchCode && (
            <Box sx={{ mb: 1 }}>
              <CodeTrendChart
                secType={nav.secType === "stock" ? "stock" : "index"}
                code={nav.searchCode}
                name={nav.findItemName(nav.searchCode)}
                height={trendExpanded ? TREND_HEIGHT_EXPANDED : TREND_HEIGHT_COLLAPSED}
                chartOptions={trendChartOptions}
                onLoaded={handleTrendLoaded}
                headerAction={
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <ToggleButton
                      value="expand"
                      size="small"
                      selected={trendExpanded}
                      onChange={() => setTrendExpanded((v) => !v)}
                      title={trendExpanded ? "Shrink the trend panel" : "Expand the trend panel"}
                      sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}
                    >
                      {trendExpanded ? <ExpandLessIcon sx={{ fontSize: 14 }} /> : <ExpandMoreIcon sx={{ fontSize: 14 }} />}
                      {trendExpanded ? "Shrink" : "Expand"}
                    </ToggleButton>
                    <Tooltip title="Clear the picked code">
                      <IconButton
                        size="small"
                        aria-label="clear code"
                        onClick={nav.onClearItemSelection}
                      >
                        <CloseIcon sx={{ fontSize: 15 }} />
                      </IconButton>
                    </Tooltip>
                  </Box>
                }
              />
            </Box>
          )}

          {/* ---- Date event bar — the shared strip (same component as the
                  News page): one dot per day with Q&A rows, scoped by the
                  classification + keyword; click toggles the day
                  filter on the feed below AND jumps the code trend's slider
                  to that date. Dates are TRADING days (backend rolls
                  holidays/weekends back, e.g. 2026-09-12 → 2026-09-11).
                  While the code trend is live the strip's VISIBLE window is
                  the trend's visible window (same start/end/length — drag
                  the trend's slider and the dots re-window in lockstep);
                  hide the trend (or pick no code) and the strip falls back
                  to its calendar range + own time slider. ---- */}
          <DateEventStrip
            events={stripEvents}
            typeMeta={{ qa: { label: "AI 问答", color: "#9a60b4" } }}
            minDate={stripMin}
            maxDate={stripMax}
            selectedId={selectedDate}
            onEventClick={(e) => {
              const d = String(e.id);
              setSelectedDate((cur) => (d === cur ? null : d));
              if (trendLive) setFocusReq({ date: d, seq: Date.now() });
            }}
            height={72}
            showLegend={false}
            emptyText={calendarLoading ? "加载中…" : "无 AI 问答记录"}
            enableZoom={!trendLive}
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
      {/* ---- Q&A content items (the feed) ---- */}
      <Box sx={{ display: "flex", alignItems: "center", mb: 1 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {scopeLabel}
          {selectedDate ? ` · ${selectedDate}` : ""}
          {search ? ` · “${search}”` : ""}
          {" · "}
          {(itemsData?.total ?? 0).toLocaleString()} 条问答
          {itemsLoading && (
            <CircularProgress size={12} sx={{ ml: 1, verticalAlign: "middle" }} />
          )}
        </Typography>
        {totalPages > 1 && (
          <Pagination
            count={totalPages}
            page={page}
            onChange={(_, v) => setPage(v)}
            size="small"
            siblingCount={1}
            boundaryCount={1}
            sx={{ ml: "auto" }}
          />
        )}
      </Box>
      {itemsError && (
        <Box component="pre" sx={{ color: "error.main", fontSize: "0.75rem", whiteSpace: "pre-wrap", mb: 1 }}>
          Failed to load Q&A: {itemsError}
        </Box>
      )}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {(itemsData?.items ?? []).map((it) => (
          <AiQaCard key={it.qa_id} item={it} />
        ))}
        {!itemsLoading && (itemsData?.items ?? []).length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
            没有匹配的 AI 问答 — 调整行业 / 日期 / 关键词后再试。
          </Typography>
        )}
      </Box>
    </SecNavShell>
  );
}
