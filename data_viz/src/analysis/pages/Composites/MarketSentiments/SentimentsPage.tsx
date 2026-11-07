/**
 * SentimentsPage — the SHARED composition of the two Market Sentiments
 * composites pages (this directory is the family's machinery; the pages
 * are the thin per-item components):
 *
 *   • ../AiMarketQa/AiMarketQaPage.tsx — /analysis/composites/ai-market-qa
 *     (AI 市场情绪问答): the LLM Q&A knowledge base feed (llm.llm_qa —
 *     AiQaCard posts), asks against INSIGHT_PAGES.marketQa.
 *   • ../MarketSentimentNews/MarketSentimentNewsPage.tsx —
 *     /analysis/composites/market-sentiment-news (市场情绪新闻): the raw
 *     news corpus feed (text.news — NewsPostCard posts), asks against
 *     INSIGHT_PAGES.marketNews.
 *
 * Each page fixes its feed SOURCE (no in-page AI/News toggle — the two
 * items are separate pages) and its chart ask's insight identity; the
 * feed heading's Ask AI "?" (SentimentFeedAskButton) follows the same
 * source identity. Both asks are keyword audits through the same
 * vocabulary (the Q&A corpus's occurrences vs the news corpus's), read
 * against each other for AI-vs-news tone divergence.
 *
 * MODULE LAYOUT (one directory, small files):
 *   • constants.ts               — shared constants + date/code helpers.
 *   • useMarketRankings.ts       — MARKET mode's feed scope: top-N HYPE +
 *                                  DRAIN industries + its scope label.
 *   • useClassificationScope.ts  — INDUSTRY mode's feed scope: the
 *                                  classification multi-select → industry
 *                                  ids + chart shades + scope label.
 *   • useChartStripSync.ts       — benchmark chart ↔ date-strip sync.
 *   • useSentimentFeed.ts        — the FIXED source's search/day-pick/
 *                                  pagination + per-source fetches.
 *   • SentimentFeedSection.tsx   — the feed JSX (heading + strip + search
 *                                  + paginated cards).
 *   • SentimentFeedAskButton.tsx — the feed heading's Ask AI "?": a
 *                                  no-chart ask following the page's
 *                                  source-split insight page with the
 *                                  CURRENT feed scope riding state.
 *   • SentimentsPage.tsx         — this composition: mode toggle, nav,
 *                                  charts, feed (parameterized by the
 *                                  page components above).
 *
 * TOP-LEVEL MODE TOGGLE — "Industry" (default) vs "Market":
 *
 * INDUSTRY mode (the original UI) mirrors Market Beta' "Benchmark
 * Attribution" mode:
 *   • the SAME shared classification nav (SecNavShell + SecClassification
 *     NavMulti — multi-select L2 industries merged non-exclusively with the
 *     RIGHT strategy→theme column, exchange filter) with an Index /
 *     Industry SEC-TYPE toggle over the trees:
 *       - Index (default) — the same market-beta nav trees: the classified
 *         indices grouped by their industry (LEFT) / strategy-theme (RIGHT)
 *         classification;
 *       - Industry — the industry-ENTITY trees (code = industry_id) under
 *         their stats.sec_classification classification, the same two-column
 *         shape the Analysis Summary page's industry scope and the MA-Spread
 *         industry mode use. An entity id IS an industry_id — the same tag
 *         llm.llm_qa / text.news rows carry — so the pick resolves the
 *         feed scope IDENTICALLY in both sec types (L2 chip → its
 *         industry_id; strategy/theme pick → the member entities' ids plus
 *         the theme's own id), and the BenchmarkPriceChart's industry
 *         shades key on the same id space; and
 *   • the SAME BenchmarkPriceChart 1st plot (benchmark dropdown over the
 *     analysis.industry_attributions benchmarks, default 000300;
 *     green/red non-this-industry shades for the picked industries;
 *     clickable to pick an as-of date).
 *
 * MARKET mode drops the classification pick entirely:
 *   • the classification nav is hidden (no pick to make), and
 *   • the plot is replaced by the SAME MarketTrendChart as Industry
 *     Sentiments' "Market Trend" mode (4 broad-market indices + trading
 *     amount, with the Overview / Hypes & Drains sub-toggle), and
 *   • the Q&A feed is scoped to the LATEST month's top-3/5 HYPE + DRAIN
 *     industries from analysis.industry_hypes_and_drains — the same
 *     rankings the plot's "Hypes & Drains" sub-view renders — PLUS the
 *     broad-market tag (BROAD_SSE) the daily ai_daily summary QA stores
 *     under, so the one Q&A that updates every trading day stays in scope
 *     (the rankings never contain it). A Top 3 / Top 5 toggle (same idiom
 *     as that sub-view) picks N per side. The feed is scoped by
 *     industry_ids — never a silent fall back to "show all": until the
 *     rankings resolve the feed fetch is deferred, and an empty ranking
 *     still scopes to the broad tag alone.
 *
 * INDUSTRY-mode feed scoping — the DataViz AI page's Q&A feed idiom loads
 * the LLM Q&A knowledge base (llm.llm_qa / llm.llm_qa_refs, written by
 * llm_agents) SCOPED BY THE SAME classification pick (see
 * useClassificationScope):
 *   • multi-selected L2 industries → their industry_ids directly;
 *   • a strategy/theme pick → the UNION of its member securities'
 *     industry_ids (resolved through the LEFT industry tree) plus the
 *     theme's own industry_id (strategy-primary indices carry their theme
 *     as industry_id in the sentiments trees);
 *   • neither picked → unscoped (the feed shows ALL industries).
 *
 * FEED SOURCE — fixed per page (see the page components above): the
 * page's feed, date strip, and keyword search all run over its source's
 * corpus. Both sources take the SAME scope (the classification pick in
 * industry mode / the top-ranked HYPE+DRAIN industries in market mode)
 * and the same selectedDate day pick; the news endpoints take the
 * multi-industry scope through their industry_ids param (comma-joined —
 * same semantics as the AI endpoints: present-even-empty scopes to
 * exactly that set).
 *
 * The DateEventStrip (the same shared strip as the AI page) shows one dot
 * per day with feed rows for the scope; clicking a dot re-anchors the feed
 * on that day (±DATE_WINDOW_DAYS — a picked day windows BOTH the AI and
 * news feeds to ±120 calendar days around itself, not a single-day filter)
 * AND jumps the benchmark chart's slider to that day. The benchmark price
 * chart's date pick writes the SAME selectedDate — clicking a chart date
 * windows the feed the same way, tying the market view and the sentiment
 * feed together. Like the DataViz AI page's code-trend ↔ strip sync, the
 * strip has NO time slider of its own: its visible window IS the benchmark
 * chart's dataZoom window (the chart reports it via onVisibleRangeChange;
 * dragging the chart's slider re-windows the dots in lockstep).
 *
 * Feed items render per source: AI → AiQaCard post cards (question,
 * snippet; click expands the full answer + per-ref citation list), News →
 * NewsPostCard social-style post cards (title, snippet; click expands the
 * full article + comments) — both with keyword search + pagination,
 * exactly like the DataViz AI / News pages.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import SecClassificationNavMulti from "@/shared/components/sec-classification/SecClassificationNavMulti";
import type { InsightPageIdentity } from "@/shared/ai-ask";
import { BenchmarkPriceChart } from "@/analysis/pages/MarketBeta/BenchmarkPriceChart";
import { MarketTrendChart } from "@/analysis/pages/MarketBeta/MarketTrendChart";
import {
  fetchIndustryAttributionBenchmarks,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { IndustryAttributionBenchmarkEntry } from "@shared/types";
import { DATE_WINDOW_DAYS, THEMES_SOURCES } from "./constants";
import { useMarketRankings } from "./useMarketRankings";
import { useClassificationScope } from "./useClassificationScope";
import { useChartStripSync } from "./useChartStripSync";
import { useSentimentFeed } from "./useSentimentFeed";
import type { FeedSource } from "./useSentimentFeed";
import SentimentFeedSection from "./SentimentFeedSection";

export default function SentimentsPage({
  source,
  title,
  dataLabel,
  chartInsight,
}: {
  /** The page's FIXED feed source — "ai" (llm.llm_qa) or "news"
   *  (text.news); the whole page (feed, strip, search, asks) runs over
   *  this corpus. */
  source: FeedSource;
  /** The SecNavShell title (the item's card title). */
  title: string;
  /** Stable slug for nav logging / error prefixes (the item's route
   *  slug, e.g. "ai-market-qa"). */
  dataLabel: string;
  /** The chart asks' insight identity — the page's own split registry
   *  page (INSIGHT_PAGES.marketQa / marketNews). */
  chartInsight: InsightPageIdentity;
}) {
  // Source-derived wording for the shell subtitle.
  const feedName = source === "ai" ? "AI Q&A (llm.llm_qa)" : "news (text.news)";
  const cardsNote =
    source === "ai"
      ? "Cards expand to the full answer + per-ref citations."
      : "Cards expand to the full article + comments.";
  // ---- Top-level mode toggle ----------------------------------------------
  // "industry" (default — the original UI) vs "market" (Market Trend plot +
  // feed scoped to the top-ranked HYPE/DRAIN industries).
  const [mode, setMode] = useState<"market" | "industry">("industry");
  // Market mode only: how many top-ranked industries PER SIDE (HYPE and
  // DRAIN) scope the feed — the same Top 3/5 idiom as the Hypes & Drains
  // sub-view of the Market Trend plot.
  const [marketTopN, setMarketTopN] = useState<3 | 5>(3);

  // Shared nav kit (same as Market Beta): loads the classification trees for
  // the ACTIVE sec_type — "index" (sector→industry + strategy→theme over the
  // classified indices) or "industry" (the industry-ENTITY trees, code =
  // industry_id) — with the exchange filter. Non-exclusive mode — both
  // columns contribute to the same merged scope. The code search renders
  // DISABLED in the shell (no per-code trend here; the L3 row is not used —
  // the scope comes from the L2 chips + strategy/themes in both sec types).
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    defaultSecType: "index",
    dataLabel,
    mutuallyExclusive: false,
    onInvalidateCache: () => {
      invalidateCacheForPrefix("/api/analysis/market-beta/");
      invalidateCacheForPrefix("/api/analysis/mov-ave-spread/");
      invalidateCacheForPrefix("/api/analysis/industry-hypes-and-drains");
      invalidateCacheForPrefix("/api/analysis/industry-attribution");
      invalidateCacheForPrefix("/api/analysis/industry-benchmark-attribution");
      invalidateCacheForPrefix("/api/ai/");
      invalidateCacheForPrefix("/api/news/");
    },
  });

  // INDUSTRY-mode scope: the classification multi-select (chart shades +
  // feed scope + label) lives in its own hook.
  const classificationScope = useClassificationScope(nav);

  // MARKET-mode scope: hypes & drains rankings (the feed-scope source).
  const { marketRanked, hdLoading, hdError, marketLabel } = useMarketRankings(
    mode,
    marketTopN,
    nav.refreshKey,
  );

  // ---- Feed scope — the combined scope the feed queries with. MARKET mode
  //      resolves it from the hypes & drains top rankings (rankings not
  //      resolved yet → null: the feed fetch is DEFERRED — a market feed
  //      never silently falls back to "show all"); INDUSTRY mode from the
  //      classification pick (useClassificationScope).
  const scope = useMemo(() => {
    if (mode === "market") {
      if (!marketRanked) return null;
      return { industry_ids: marketRanked.ids };
    }
    return classificationScope.industryScope;
  }, [mode, marketRanked, classificationScope.industryScope]);

  const scopeLabel = mode === "market" ? marketLabel : classificationScope.scopeLabel;

  // ---- Chart ↔ date-strip sync (see useChartStripSync) ---------------------
  const [selectedBenchmarkCode, setSelectedBenchmarkCode] = useState<string | null>("000300");
  const {
    chartRange,
    chartSpan,
    focusReq,
    setFocusReq,
    handleChartRange,
    handleChartLoaded,
  } = useChartStripSync(selectedBenchmarkCode, mode);

  // ---- Feed (the page's fixed source + search/day-pick + fetches) — see
  //      useSentimentFeed. The strip's dot picks also jump the benchmark
  //      chart's slider in industry mode.
  const feedState = useSentimentFeed({
    scope,
    refreshKey: nav.refreshKey,
    chartRange,
    chartSpan,
    source,
  });
  const handleStripFocusJump = (date: string) =>
    setFocusReq({ date, seq: Date.now() });

  // ---- Benchmark dropdown (same as Market Beta attribution mode) --
  const [attributionBenchmarks, setAttributionBenchmarks] = useState<
    IndustryAttributionBenchmarkEntry[]
  >([]);
  // The selected benchmark code (drives the price chart). Defaults to 000300
  // (沪深300 — the most common broad-market index).
  useEffect(() => {
    let cancelled = false;
    fetchIndustryAttributionBenchmarks()
      .then((resp) => {
        if (cancelled) return;
        setAttributionBenchmarks(resp.benchmarks);
      })
      .catch(() => {
        // Non-fatal — the dropdown is empty but the default 000300 still
        // works via the price endpoint.
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <SecNavShell
      nav={nav}
      title={title}
      backPath="/analysis/composites"
      backLabel="back to composites"
      subtitle={`${scopeLabel}${feedState.selectedDate ? ` · ${feedState.selectedDate} ±${DATE_WINDOW_DAYS}天` : " · 全部日期"} — the ${feedName}
            feed. Market / Industry toggle. Industry (default): the
            classification pick scopes BOTH the benchmark price chart's
            industry shades AND the sentiment feed below — tick multiple
            industry chips (across sectors — picked industries persist) and
            optionally a strategy/theme; both columns merge into one scope.
            The Index / Industry sec-type toggle switches the classification
            trees: Index groups the classified indices by their industry /
            strategy-theme; Industry shows the industry entities (code =
            industry_id) under their classification — a pick scopes the feed
            identically in both (the entity ids ARE the feed's industry
            tags). Click a date on the benchmark chart (or a calendar dot)
            to window the feed to ±120 days around that day; the strip has
            no slider of its own — the benchmark chart's slider windows the
            dots. Market: the classification nav is replaced by the Market
            Trend plot (broad-market indices + the Overview / Hypes & Drains
            sub-toggle) and the feed is scoped to the latest month's Top 3/5
            HYPE + DRAIN industries (the Top 3 / 5 toggle picks N per side)
            plus the broad-market daily QA (大盘 · BROAD_SSE); the strip
            falls back to its own slider. ${cardsNote} The heading's "?"
            asks with this page's keyword-audit metrics (read against the
            sibling page's audit for AI-vs-news tone divergence).`}
      // Sec_type toggle: Index (classified indices) vs Industry (the
      // industry entities) — both scope the feed the same way. No code
      // search — the page has no per-code trend (renders DISABLED).
      secTypes={["index", "industry"]}
      disableSearch
      refreshTooltip="Refresh classification trees + the feed (bypass cache)"
      errorPrefix={`${dataLabel} data`}
      // Market mode renders NO navigator — an empty fragment (not null, so
      // the shell doesn't fall back to the default single-select nav).
      navSlot={
        mode === "industry" ? (
          <SecClassificationNavMulti
            sectors={nav.sectors}
            sectorId={nav.sectorId}
            onSectorChange={classificationScope.handleSectorChange}
            selectedIndustrySlugs={classificationScope.selectedIndustrySlugs}
            onMultiIndustryChange={classificationScope.setSelectedIndustrySlugs}
            exchange={nav.exchange}
            onExchangeChange={nav.handleExchangeChange}
            strategies={nav.strategies}
            strategyId={nav.strategyId}
            themeSlug={nav.themeSlug}
            onStrategyChange={nav.handleStrategyChange}
            onThemeChange={nav.handleThemeChange}
            loading={nav.loading}
          />
        ) : (
          <></>
        )
      }
    >
      {/* ---- Top-level mode toggle — Market vs Industry (Industry is the
              default). In Market mode a second toggle picks the feed's
              top-N per side (same 3/5 idiom as the Hypes & Drains
              sub-view's Show control). ---- */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
          mb: 1,
          mt: 0.5,
        }}
      >
        <ToggleButtonGroup
          value={mode}
          exclusive
          size="small"
          onChange={(_, v: "market" | "industry" | null) => {
            if (v) setMode(v);
          }}
        >
          <ToggleButton value="market">Market</ToggleButton>
          <ToggleButton value="industry">Industry</ToggleButton>
        </ToggleButtonGroup>
        {mode === "market" && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600 }}>
              {source === "ai" ? "QA scope:" : "Feed scope:"}
            </Typography>
            <ToggleButtonGroup
              value={marketTopN}
              exclusive
              size="small"
              onChange={(_e, v) => { if (v !== null) setMarketTopN(v as 3 | 5); }}
              sx={{ height: 28 }}
            >
              <ToggleButton value={3} sx={{ px: 1, py: 0.25, fontSize: "0.7rem", textTransform: "none" }}>
                Top 3
              </ToggleButton>
              <ToggleButton value={5} sx={{ px: 1, py: 0.25, fontSize: "0.7rem", textTransform: "none" }}>
                Top 5
              </ToggleButton>
            </ToggleButtonGroup>
          </Box>
        )}
      </Box>
      {/* ---- 1st plot: INDUSTRY mode renders the benchmark price chart
              (same as Market Beta "Benchmark Attribution" mode) with
              the benchmark dropdown above it — clicking a date sets the
              as-of date for the sentiment feed below. MARKET mode swaps it
              for the SAME MarketTrendChart as Market Beta' "Market
              Trend" mode (no benchmark dropdown of its own — its sub-views
              own their controls; a rankings fetch error shows above it and
              defers the feed). ---- */}
      {mode === "industry" ? (
        <>
          <Box
            sx={{
              display: "flex",
              justifyContent: "flex-end",
              alignItems: "center",
              gap: 2,
              flexWrap: "wrap",
              mb: 1,
            }}
          >
            <Autocomplete
              size="small"
              options={attributionBenchmarks}
              getOptionLabel={(b) =>
                `${b.benchmark_name} (${b.benchmark_code})${b.is_broad_market === true ? " ★" : ""}`
              }
              isOptionEqualToValue={(a, b) => a.benchmark_code === b.benchmark_code}
              value={
                attributionBenchmarks.find(
                  (b) => b.benchmark_code === selectedBenchmarkCode,
                ) ?? null
              }
              onChange={(_, newValue) => {
                if (newValue) setSelectedBenchmarkCode(newValue.benchmark_code);
              }}
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  label="Benchmark (★ = broad-market)"
                  sx={{ minWidth: 240, "& .MuiOutlinedInput-root": { py: 0.25 } }}
                />
              )}
              sx={{ minWidth: 240, maxWidth: 340 }}
            />
          </Box>
          <BenchmarkPriceChart
            benchmarkCode={selectedBenchmarkCode}
            selectedDate={feedState.selectedDate}
            onDateSelect={(d) => feedState.setSelectedDate(d)}
            selectedIndustries={classificationScope.selectedIndustries}
            onLoaded={handleChartLoaded}
            onVisibleRangeChange={handleChartRange}
            focusDateRequest={focusReq}
            // The chart ask resolves THIS page's keyword-audit registry
            // (chartInsight — the page's own split item identity), not the
            // benchmark-price numerics the chart carries on Market Beta.
            // The feed heading's own ask (SentimentFeedAskButton) follows
            // the same source identity.
            insightPage={chartInsight}
          />
        </>
      ) : (
        <>
          {hdError && (
            <Alert severity="error" sx={{ py: 0.5, mb: 1 }}>
              Failed to load top-ranked industries (feed deferred): {hdError}
            </Alert>
          )}
          <MarketTrendChart insightPage={chartInsight} />
        </>
      )}

      {/* ---- Sentiment feed (the DataViz AI / News pages' idiom), scoped by
              the mode: the classification pick (industry) or the top-ranked
              HYPE/DRAIN industries (market). ---- */}
      <SentimentFeedSection
        mode={mode}
        hdLoading={hdLoading}
        scopeLabel={scopeLabel}
        feedState={feedState}
        onStripFocusJump={handleStripFocusJump}
      />
    </SecNavShell>
  );
}
