/**
 * Industry Sentiments analysis page (default export).
 *
 * Plots each industry's member INDEX VALUES directly, rebased to 100 at the
 * start of the displayed (zoom) window. Rebased-to-100 makes member indices
 * comparable regardless of absolute price level — e.g. CSI 500 (~5500pts)
 * and SSE 50 (~2600pts) plot on a common scale, so a +10% move on either
 * looks equally large. The LINE rebasing is computed CLIENT-SIDE from raw
 * daily closes.
 *
 * ADDITIONALLY overlays the server-precomputed MEAN and ±1σ VARIANCE band
 * across member indices for the user-selected pool_size slice
 * (small <51 stocks / mid <301 / large / all). The mean/var are anchored at
 * the START OF ALL HISTORY (per-index first available close, fixed server-side).
 * When the client-side slider narrows, the lines re-rebase to the slider's
 * window-start but the mean/var overlay STAYS anchored at history start —
 * they are aligned only at full slider range.
 *
 * BROAD-MARKET indices (BROAD_CSI, BROAD_SSE, BROAD_SZSE, BROAD_STAR) are
 * classified as industries under the FIN sector and are aggregated IDENTICALLY.
 *
 * COMPOSITION-ONLY: the API only returns indices that have at least one
 * stats.sec_composition snapshot. Indices WITHOUT composition data are never
 * loaded — every member index plotted here has a known stock_num.
 *
 * Per industry (one plot):
 *   • Lines  = one per member index (filtered by pool_size toggle), rebased
 *              to 100 at the start of the visible (zoom) window.
 *   • Overlay = mean line (dashed, thicker) + ±1σ variance band (shaded)
 *              from the precomputed aggregation for the selected pool_size.
 *   • Slider = bottom, controls the visible range [startIdx, endIdx] in the
 *              shared date axis. Rebase point recomputes for the LINES only.
 *   • Toggle = All / Small / Mid / Large pool-size filter (filters both
 *              lines and overlay).
 *   • Tooltip = per-index actual close (raw value) + rebased % + stock_num.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  CircularProgress,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { useStore } from "@/store/filters";
import {
  fetchIndustrySentimentsThemes,
  fetchIndustrySentimentsChart,
  fetchIndustrySentimentsChartByCode,
  fetchIndustryAttributionBenchmarks,
  fetchIndustrySentimentsStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  IndustrySentimentsChartResponse,
  IndustrySentimentsIndex,
  IndustryAttributionBenchmarkEntry,
  SectorNode,
  StrategyNode,
} from "@shared/types";
import { IndustrySentimentsPlot } from "./IndustrySentimentsPlot";
import { BenchmarkPriceChart } from "./BenchmarkPriceChart";
import { IndustryBenchmarkAttributionChart } from "./IndustryBenchmarkAttributionChart";
import { IndustryEtfPriceChart } from "./IndustryEtfPriceChart";
import { IndustryEtfContributionChart } from "./IndustryEtfContributionChart";
import { MarketTrendChart } from "./MarketTrendChart";
import { IndexAllocationView } from "./IndexAllocationView";

export default function IndustrySentimentsPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  // Multi-select: list of selected industry slugs. Persists across sector
  // switches so the user can pick industries from multiple sectors.
  const [selectedIndustrySlugs, setSelectedIndustrySlugs] = useState<string[]>([]);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  // Parallel strategy → theme state (RIGHT column of the two-column selector).
  // Mutually exclusive with sector/industry: when strategyId is set, the
  // industry multi-select is cleared and vice versa.
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);

  // L3 security-level selection: a list of selected index codes. When non-
  // empty, the chart narrows to show ONLY these individual index codes
  // (filtering on top of the multi-industry view). Cleared by clicking the
  // "All" chip in the L3 row or by switching industry. Multi-select: tick
  // multiple L3 chips (across the picked industries) to merge specific
  // member indices into one plot.
  const [selectedItemCodes, setSelectedItemCodes] = useState<string[]>([]);

  const [chartDataList, setChartDataList] = useState<IndustrySentimentsChartResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Top-level view mode toggle: "correlation" shows the existing
  // multi-line price + PE + amount + pairwise-correlation charts; "attribution"
  // swaps them for a benchmark price chart (1st plot, clickable to pick a
  // date) + one per-industry attribution bar chart per selected industry
  // (2nd plot onward — sourced from analysis.industry_attributions).
  const [viewMode, setViewMode] = useState<"correlation" | "attribution" | "etf_contribution" | "market_trend" | "index_allocation">("correlation");
  // Benchmark dropdown list (fetched once when entering attribution mode).
  const [attributionBenchmarks, setAttributionBenchmarks] = useState<IndustryAttributionBenchmarkEntry[]>([]);
  // The selected benchmark code (drives the 1st plot). Defaults to 000300
  // (沪深300 — the most common broad-market index).
  const [selectedBenchmarkCode, setSelectedBenchmarkCode] = useState<string | null>("000300");
  // The as-of date for the attribution plots — set by clicking a date on the
  // benchmark price chart (1st plot). Null → latest available.
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const slugToIndustryId = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sectors) {
      for (const ind of s.industries) {
        m.set(ind.industry_slug, ind.industry_id);
      }
    }
    return m;
  }, [sectors]);

  // Map selected slugs → industry IDs (dropping any slug that no longer maps,
  // e.g. if the taxonomy was refreshed and the industry disappeared).
  const selectedIndustryIds = useMemo(
    () =>
      selectedIndustrySlugs
        .map((slug) => slugToIndustryId.get(slug))
        .filter((id): id is string => Boolean(id)),
    [selectedIndustrySlugs, slugToIndustryId],
  );

  // Snapshot of which industry_id each slug maps to (for the merged-chart
  // industry-label lookup). Kept in sync with selectedIndustrySlugs so the
  // chart can prefix each index with its source industry.
  const slugToIndustryLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sectors) {
      for (const ind of s.industries) {
        m.set(ind.industry_slug, ind.industry_label);
      }
    }
    return m;
  }, [sectors]);

  // Map strategy theme industry_id → display label (for the merged-chart
  // label lookup when strategy themes are fetched by industry_id). Built from
  // the strategies tree (RIGHT column).
  const strategyThemeIdToLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of strategies) {
      for (const th of s.industries) {
        m.set(th.industry_id, th.industry_label);
      }
    }
    return m;
  }, [strategies]);

  // Compute strategy theme industry_ids from strategyId/themeSlug. When a
  // strategy is selected, its themes' industry_ids are fetched the SAME way
  // as industry IDs (getIndustrySentimentsChart queries by industry_id, and
  // strategy-primary indices carry their theme as industry_id). When no
  // theme is selected, ALL themes under the strategy are included.
  const selectedStrategyThemeIds = useMemo(() => {
    if (!strategyId) return [];
    const strat = strategies.find((s) => s.sector_id === strategyId);
    if (!strat) return [];
    if (themeSlug) {
      const th = strat.industries.find((t) => t.industry_slug === themeSlug);
      return th ? [th.industry_id] : [];
    }
    return strat.industries.map((t) => t.industry_id);
  }, [strategyId, themeSlug, strategies]);

  // Array of { id, label } for the BenchmarkPriceChart's selectedIndustries
  // prop. Resolves each selected industry_id to its display label. Includes
  // BOTH industry IDs (LEFT column) and strategy theme IDs (RIGHT column).
  const selectedIndustries = useMemo(() => {
    const result = selectedIndustryIds.map((id) => {
      const slugEntry = Array.from(slugToIndustryId.entries()).find(
        ([, i]) => i === id,
      );
      const label = slugEntry
        ? (slugToIndustryLabel.get(slugEntry[0]) ?? id)
        : id;
      return { id, label };
    });
    for (const id of selectedStrategyThemeIds) {
      result.push({
        id,
        label: strategyThemeIdToLabel.get(id) ?? id,
      });
    }
    return result;
  }, [selectedIndustryIds, slugToIndustryId, slugToIndustryLabel, selectedStrategyThemeIds, strategyThemeIdToLabel]);

  // Load themes (LEFT column — industry taxonomy tree) and the parallel
  // strategy tree (RIGHT column) on mount, on refresh, AND when the exchange
  // filter changes — so the WHOLE classification nav (sector/industry/
  // strategy/theme chips + L3 item chips) refreshes to respect the selected
  // exchange (e.g. HK indices are excluded when "All (primary)" is selected).
  // Both are fetched in parallel.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchIndustrySentimentsThemes(exchange),
      fetchIndustrySentimentsStrategyThemes(exchange),
    ])
      .then(([t, st]) => {
        if (cancelled) return;
        setSectors(t);
        setStrategies(st);
        // Prune stale multi-select industries that no longer exist in the
        // filtered tree (e.g. switching to HK drops mainland-only industries).
        const sectorIds = new Set(t.map((s) => s.sector_id));
        const validSlugs = new Set<string>();
        for (const s of t) {
          for (const ind of s.industries) validSlugs.add(ind.industry_slug);
        }
        if (sectorId && !sectorIds.has(sectorId)) {
          setSectorId(null);
        }
        setSelectedIndustrySlugs((prev) => {
          const next = prev.filter((slug) => validSlugs.has(slug));
          return next.length === prev.length ? prev : next;
        });
        // Clear stale strategy/theme selection if not in the filtered tree.
        if (strategyId && !st.some((s) => s.sector_id === strategyId)) {
          setStrategyId(null);
          setThemeSlug(null);
        }
        // Seed the multi-select with the first industry of the first sector
        // so the page shows data immediately on FIRST load only (sectorId ==
        // null AND no prior selection). On subsequent exchange changes the
        // pruned selection above is kept (the user may have picked cross-
        // border industries deliberately).
        if (t.length > 0 && sectorId == null && selectedIndustrySlugs.length === 0) {
          setSectorId(t[0].sector_id);
          const firstSlug = t[0].industries[0]?.industry_slug ?? null;
          if (firstSlug) setSelectedIndustrySlugs([firstSlug]);
        }
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, exchange]);

  // Combined set of industry IDs to fetch: selected industries (LEFT column)
  // PLUS selected strategy theme IDs (RIGHT column). Strategy themes are
  // fetched the SAME way as industries — getIndustrySentimentsChart queries
  // by industry_id, and strategy-primary indices carry their theme as
  // industry_id. When neither is selected, fall back to finding industries
  // from L3-selected codes.
  const effectiveIndustryIds = useMemo(() => {
    const ids = new Set<string>();
    for (const id of selectedIndustryIds) ids.add(id);
    for (const id of selectedStrategyThemeIds) ids.add(id);
    if (ids.size === 0 && selectedItemCodes.length > 0) {
      const targetCodes = new Set(
        selectedItemCodes.map((c) => c.toUpperCase()),
      );
      for (const sector of sectors) {
        for (const ind of sector.industries) {
          if (ind.items.some((it) => targetCodes.has(it.code.toUpperCase()))) {
            ids.add(ind.industry_id);
          }
        }
      }
    }
    return Array.from(ids);
  }, [selectedIndustryIds, selectedStrategyThemeIds, selectedItemCodes, sectors]);

  // Codes from `selectedItemCodes` that are NOT in any industry in the
  // sectors tree (strategy-primary only). These must be fetched via the
  // code-based endpoint. When strategy themes are already being fetched by
  // ID (selectedStrategyThemeIds non-empty), strategy codes are in the
  // response — no need to fetch them by code, so return [].
  const strategyOnlyCodes = useMemo(() => {
    if (selectedItemCodes.length === 0) return [];
    if (selectedStrategyThemeIds.length > 0) return [];
    const inSectors = new Set<string>();
    for (const sector of sectors) {
      for (const ind of sector.industries) {
        for (const it of ind.items) {
          inSectors.add(it.code.toUpperCase());
        }
      }
    }
    return selectedItemCodes.filter(
      (c) => !inSectors.has(c.toUpperCase()),
    );
  }, [selectedItemCodes, sectors, selectedStrategyThemeIds]);

  // Fetch chart data: by industry (normal/multi-select) PLUS by code (for
  // strategy-primary indexes not in the sectors tree), or clear when nothing
  // selected.
  const effectiveIdsKey = effectiveIndustryIds.join(",");
  const strategyCodesKey = strategyOnlyCodes.join(",");
  useEffect(() => {
    if (effectiveIndustryIds.length === 0 && strategyOnlyCodes.length === 0) {
      setChartDataList([]);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    const byIndustry = effectiveIndustryIds.map((id) =>
      fetchIndustrySentimentsChart(id),
    );
    const byCode = strategyOnlyCodes.map((code) =>
      fetchIndustrySentimentsChartByCode(code),
    );
    Promise.all([...byIndustry, ...byCode])
      .then((results) => {
        if (cancelled) return;
        setChartDataList(results);
        setChartLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setChartError(e.message);
        setChartLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveIdsKey, strategyCodesKey, refreshKey]);

  // Merge multiple industries' chart data into a single
  // IndustrySentimentsChartResponse. When only one industry is selected, the
  // merge is a passthrough (preserves the mean/var overlay). When multiple
  // are selected:
  //   • indices are concatenated (de-duplicated by code — an index may carry
  //     multiple industry tags and would otherwise appear once per tag).
  //   • Each index's name is prefixed with "[industry_short] " so the legend
  //     and tooltip identify which industry it came from.
  //   • The merged `aggregation` is empty (per-industry means can't be combined
  //     across industries), BUT the un-merged `chartDataList` is passed
  //     through to the plot, which builds per-industry mean curves from it.
  //   • benchmarks come from the first response (they're identical across
  //     industries — same hardcoded broad-market list).
  //   • industry_label lists all source industries joined by " + ".
  // L3 individual-index selection narrows the DISPLAYED LINES only; the
  // per-industry mean/±σ aggregation always aggregates ALL member indices.
  const mergedChartData = useMemo<IndustrySentimentsChartResponse | null>(() => {
    if (chartDataList.length === 0) return null;
    // Set of selected L3 codes (uppercased) for filtering. Empty = no filter.
    // L3 individual-index selection NARROWS THE DISPLAYED LINES ONLY — the
    // per-industry mean/±σ aggregation is ALWAYS preserved (it aggregates ALL
    // member indices of the selected industries, not just the L3 subset), so
    // the "Mean only" overlay and PE/Trading-Amount sub-plots stay anchored
    // to the full industry pool.
    const selectedCodeSet = new Set(
      selectedItemCodes.map((c) => c.toUpperCase()),
    );
    const hasCodeFilter = selectedCodeSet.size > 0;
    if (chartDataList.length === 1) {
      const base = chartDataList[0];
      // Narrow displayed lines to the L3-selected codes, but KEEP aggregation
      // (mean/±σ still aggregates every member index of the industry).
      if (hasCodeFilter) {
        const filtered = base.indices.filter((idx) =>
          selectedCodeSet.has(idx.code.toUpperCase()),
        );
        return { ...base, indices: filtered };
      }
      return base;
    }

    const multi = chartDataList.length > 1;
    const seenCodes = new Set<string>();
    const mergedIndices: IndustrySentimentsIndex[] = [];
    for (const d of chartDataList) {
      // Resolve the short industry label for the prefix. Match by industry_id
      // (the chart response carries industry_id, not slug). Check BOTH the
      // industry tree (sectors) and the strategy tree (strategies).
      const slugEntry = Array.from(slugToIndustryId.entries()).find(
        ([, id]) => id === d.industry_id,
      );
      const fullLabel = slugEntry
        ? (slugToIndustryLabel.get(slugEntry[0]) ?? d.industry_label)
        : (strategyThemeIdToLabel.get(d.industry_id) ?? d.industry_label);
      const shortLabel = (fullLabel || d.industry_id).split("  ")[0] || d.industry_id;
      for (const idx of d.indices) {
        if (seenCodes.has(idx.code)) continue;
        seenCodes.add(idx.code);
        mergedIndices.push(
          multi
            ? { ...idx, name: `[${shortLabel}] ${idx.name}` }
            : idx,
        );
      }
    }
    // When L3 codes are selected, narrow to just them.
    const finalIndices = hasCodeFilter
      ? mergedIndices.filter((idx) =>
          selectedCodeSet.has(idx.code.toUpperCase()),
        )
      : mergedIndices;
    return {
      industry_id: chartDataList.map((d) => d.industry_id).join(","),
      industry_label: chartDataList
        .map((d) => {
          const slugEntry = Array.from(slugToIndustryId.entries()).find(
            ([, id]) => id === d.industry_id,
          );
          return slugEntry
            ? (slugToIndustryLabel.get(slugEntry[0]) ?? d.industry_label)
            : (strategyThemeIdToLabel.get(d.industry_id) ?? d.industry_label);
        })
        .join(" + "),
      indices: finalIndices,
      aggregation: [], // dropped — see comment above
      benchmarks: chartDataList[0]?.benchmarks ?? [],
    };
  }, [chartDataList, slugToIndustryId, slugToIndustryLabel, strategyThemeIdToLabel, selectedItemCodes]);

  const multiIndustry = chartDataList.length > 1;

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/analysis/industry-sentiments/");
    invalidateCacheForPrefix("/api/analysis/industry-correlations");
    invalidateCacheForPrefix("/api/analysis/industry-benchmark-attribution");
    invalidateCacheForPrefix("/api/analysis/industry-attribution-bars");
    invalidateCacheForPrefix("/api/analysis/industry-etf-contribution");
    // Index Allocation view fetches per-code attribution + charts from the
    // perf-attr endpoints — invalidate those too so Refresh covers all modes.
    invalidateCacheForPrefix("/api/analysis/perf-attr/");
    setRefreshKey((k) => k + 1);
  };

  // Fetch the benchmark list for the attribution dropdown once when the user
  // first enters attribution mode (or on refresh). The list is small (~145
  // codes) and stable so we only fetch it once per refresh cycle.
  useEffect(() => {
    if (viewMode !== "attribution" || attributionBenchmarks.length > 0) return;
    let cancelled = false;
    fetchIndustryAttributionBenchmarks()
      .then((resp) => {
        if (cancelled) return;
        setAttributionBenchmarks(resp.benchmarks);
      })
      .catch(() => {
        // Non-fatal — the dropdown will be empty but the default
        // selectedBenchmarkCode (000300) still works via the price endpoint.
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, refreshKey]);

  // Sector change only updates the row-2 browsing context — it does NOT clear
  // the multi-select selection (industries picked from other sectors persist).
  // Non-exclusive mode: engaging the LEFT column (sector/industry) does NOT
  // clear the RIGHT column (strategy/theme) — both can be active at once.
  const handleSectorChange = (id: string | null) => {
    setSectorId(id);
    setSelectedItemCodes([]);
  };
  const handleMultiIndustryChange = (slugs: string[]) => {
    setSelectedIndustrySlugs(slugs);
    // Prune any selected L3 codes that no longer belong to a selected
    // industry — keeps the L3 filter consistent with the active selection.
    if (slugs.length === 0) {
      setSelectedItemCodes([]);
    } else {
      const slugSet = new Set(slugs);
      const validCodes = new Set<string>();
      for (const s of sectors) {
        for (const ind of s.industries) {
          if (!slugSet.has(ind.industry_slug)) continue;
          for (const it of ind.items) validCodes.add(it.code.toUpperCase());
        }
      }
      setSelectedItemCodes((prev) =>
        prev.filter((c) => validCodes.has(c.toUpperCase())),
      );
    }
  };
  // Kept for ThemeSelector's single-select prop signature (no-op in multi mode).
  const handleIndustryChange = () => {
    /* no-op — multi-select mode uses handleMultiIndustryChange */
  };
  // Clicking a strategy/theme chip engages the RIGHT column. Non-exclusive
  // mode: selecting in the RIGHT column does NOT clear the LEFT column
  // (sector + industry multi-select) — both contribute to the merged plot.
  const handleStrategyChange = (id: string | null) => {
    setStrategyId(id);
    if (!id) setThemeSlug(null);
    setSelectedItemCodes([]);
  };
  const handleThemeChange = (slug: string | null) => {
    setThemeSlug(slug);
    setSelectedItemCodes([]);
  };
  const handleExchangeChange = (ex: string | null) => {
    setExchange(ex);
  };

  // Header label: reflects BOTH industry (LEFT) and strategy (RIGHT) selections.
  // When 0 selected → "Select industries"; when 1 → the full path; when >1 →
  // "N selected". Strategy selection is appended when active.
  const headerLabel = (() => {
    const indPart =
      selectedIndustrySlugs.length === 0
        ? null
        : selectedIndustrySlugs.length === 1
          ? (() => {
              const slug = selectedIndustrySlugs[0];
              const sector = sectors.find((s) =>
                s.industries.some((i) => i.industry_slug === slug),
              );
              const ind = sector?.industries.find((i) => i.industry_slug === slug);
              return ind
                ? `${sector?.sector_label ?? ""} / ${ind.industry_label}`
                : "1 industry";
            })()
          : `${selectedIndustrySlugs.length} industries`;
    const strat = strategyId
      ? strategies.find((s) => s.sector_id === strategyId)
      : null;
    const stratPart = strat
      ? themeSlug
        ? `${strat.sector_label} / ${strat.industries.find((t) => t.industry_slug === themeSlug)?.industry_label ?? strat.sector_label}`
        : strat.sector_label
      : null;
    if (indPart && stratPart) return `${indPart} + ${stratPart}`;
    if (indPart) return indPart;
    if (stratPart) return stratPart;
    return "Select industries";
  })();

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <IconButton
              onClick={() => navigate("/analysis/commons")}
              size="small"
              aria-label="back to commons"
            >
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Industry Sentiments
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — each member index's daily close (actual value shown in
            tooltip), rebased to 100 at the start of the visible (zoom) window.
            <strong> Multi-select:</strong> tick multiple industry chips (across
            sectors — switch the active sector to browse, picked industries
            persist) to merge their member indices into one plot. Toggle pool
            size to filter by member count (small &lt;51, mid 51-180, large
            &gt;180). The dashed mean line and ±1σ band are precomputed
            server-side (anchored at history start — aligned with lines only at
            full slider range). In single-industry mode the mean/var overlay is
            always shown; in multi-industry mode, toggle <strong>Mean only</strong>
            to hide the per-index lines and render one mean curve PER industry
            (each in a distinct color with its own ±1σ band) for cross-industry
            comparison. Below the price chart, two sub-plots show the
            cross-sectional <strong>mean PE</strong> and <strong>mean trading
            amount</strong> (in yuan, displayed in 亿元) of member indices — in single-industry mode one
            line per pool_size, in multi-industry mode one line per industry (for
            the selected pool). Only indices WITH composition data are shown;
            indices without any composition snapshot are excluded entirely.
            Broad-market indices (BROAD_CSI/SSE/SZSE/STAR) appear under the FIN
            sector. <strong>L3 Index multi-select:</strong> tick multiple index
            chips (across the picked industries) to narrow the displayed lines
            to just those member indices; click <strong>All</strong> to clear
            the filter. <strong>All &lt;industry&gt;</strong> chips (one per
            selected industry) appear alongside — click one to DROP that whole
            industry from the selection. The per-industry mean/±σ overlay
            always aggregates ALL member indices of the selected industries
            (L3 narrowing affects displayed lines only).
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh industry-sentiments data (bypass cache)"
        />
      </Box>

      <SecClassificationNav
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={selectedIndustrySlugs[0] ?? null}
        exchange={exchange}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
        multiSelect
        selectedIndustrySlugs={selectedIndustrySlugs}
        onMultiIndustryChange={handleMultiIndustryChange}
        strategies={strategies}
        strategyId={strategyId}
        themeSlug={themeSlug}
        onStrategyChange={handleStrategyChange}
        onThemeChange={handleThemeChange}
        itemKind="Index"
        multiSelectItems
        selectedItemCodes={selectedItemCodes}
        onMultiItemSelected={setSelectedItemCodes}
        showAllIndustryChips
        mutuallyExclusive={false}
        loading={loading}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load industry-sentiments data: {error}
        </Alert>
      )}
      {!loading && !error && effectiveIndustryIds.length === 0 && selectedItemCodes.length === 0 && (
        <Alert severity="warning">Select one or more industries to see the member indices.</Alert>
      )}
      {!loading && !error && (effectiveIndustryIds.length > 0 || selectedItemCodes.length > 0) && (
        <>
          {/* Top-level view-mode toggle: "Industry Correlation" (existing
              multi-line price + PE + amount + pairwise-correlation charts)
              vs "Benchmark Attribution" (benchmark price chart as 1st plot
              + per-industry attribution bar charts from
              analysis.industry_attributions as 2nd+ plots). In attribution
              mode a benchmark dropdown selects the benchmark for the 1st
              plot; clicking a date on the 1st plot sets the as-of date for
              the attribution bar charts below. */}
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
              value={viewMode}
              exclusive
              size="small"
              onChange={(_, v: "correlation" | "attribution" | "etf_contribution" | "market_trend" | "index_allocation" | null) => {
                if (v) setViewMode(v);
              }}
            >
              <ToggleButton value="correlation">Industry Correlation</ToggleButton>
              <ToggleButton value="attribution">Benchmark Attribution</ToggleButton>
              <ToggleButton value="etf_contribution">ETF Contribution</ToggleButton>
              <ToggleButton value="market_trend">Market Trend</ToggleButton>
              <ToggleButton value="index_allocation">Index Allocation</ToggleButton>
            </ToggleButtonGroup>
            {viewMode === "attribution" && (
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
            )}
          </Box>

          {viewMode === "correlation" && (
            <>
              {chartLoading && (
                <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
                  <CircularProgress size={28} />
                </Box>
              )}
              {chartError && (
                <Alert severity="error" sx={{ py: 0.5 }}>{chartError}</Alert>
              )}
              {!chartLoading && !chartError && mergedChartData && (
                <IndustrySentimentsPlot
                  data={mergedChartData}
                  themeMode={themeMode}
                  multiIndustry={multiIndustry}
                  numIndustries={chartDataList.length}
                  chartDataList={chartDataList}
                  selectedIndustryIds={effectiveIndustryIds}
                  selectedItemCodes={selectedItemCodes}
                />
              )}
            </>
          )}

          {viewMode === "attribution" && (
            <Stack spacing={1.5}>
              {/* 1st plot: Benchmark price chart with non-this-industry green/red shades.
                  The shades show, for each selected industry, what the
                  benchmark's price would be if the industry's shared stocks
                  were removed. Green shade = non-industry above benchmark
                  (industry was a drag); red shade = below (industry was a
                  boost). Toggle switches between Percentage (rebased to 100)
                  and Absolute (raw prices). The chart is clickable — clicking
                  a date sets the as-of date for the bar charts below. */}
              <BenchmarkPriceChart
                benchmarkCode={selectedBenchmarkCode}
                themeMode={themeMode}
                selectedDate={selectedDate}
                onDateSelect={(d) => setSelectedDate(d)}
                selectedIndustries={selectedIndustries}
              />

              {/* 2nd+ plots: Per-industry benchmark attribution bar charts.
                  One plot per selected industry; each bar = one benchmark.
                  Shows the benchmark's contribution (return × shared weight)
                  and both shared weight metrics as grouped bars. Includes
                  an All/Sector toggle (identical to PerfAttr's fluctuation
                  chart) to show/hide broad-market benchmarks. The selected
                  benchmark (from the dropdown) is highlighted. Same date as
                  the BenchmarkPriceChart above. */}
              {selectedIndustries.map((ind) => (
                <IndustryBenchmarkAttributionChart
                  key={ind.id}
                  industryId={ind.id}
                  industryLabel={ind.label}
                  date={selectedDate}
                  themeMode={themeMode}
                  selectedBenchmarkCode={selectedBenchmarkCode}
                />
              ))}
            </Stack>
          )}

          {viewMode === "etf_contribution" && (
            <Stack spacing={1.5}>
              {/* 1st plot: Multi-ETF price chart with cascading rebase.
                  Each ETF tracking a member index of the selected industries
                  is plotted as a separate line, rebased to 100 at its own
                  first available date. Later-listed ETFs start at the MEAN
                  of already-active ETFs on their first date (cascading
                  rebase) so they blend in rather than jumping to 100. The
                  chart is clickable — clicking a date sets the as-of date
                  for the bar charts below.

                  In-plot controls (top-right): a "Trading Amt" MA dropdown
                  (MA5 / MA20) and a merged toggle that switches between the
                  price-trend curve being prominent (trading-amt bars + MA
                  lowkey) vs the trading-amt bars + MA being prominent (price
                  curve lowkey). The plot is never hidden — only the relative
                  emphasis flips. */}
              <IndustryEtfPriceChart
                industryIds={selectedIndustryIds}
                themeMode={themeMode}
                selectedDate={selectedDate}
                onDateSelect={(d) => setSelectedDate(d)}
              />

              {/* 2nd+ plots: Per-industry ETF contribution bar charts.
                  One plot per selected industry; each bar = one ETF showing
                  its trading amount (capital flow, colored by return
                  direction) and its % share of the industry total ETF
                  trading amount. Same date as the IndustryEtfPriceChart
                  above. */}
              {selectedIndustries.map((ind) => (
                <IndustryEtfContributionChart
                  key={ind.id}
                  industryId={ind.id}
                  industryLabel={ind.label}
                  date={selectedDate}
                  themeMode={themeMode}
                />
              ))}
            </Stack>
          )}

          {viewMode === "market_trend" && (
            <MarketTrendChart themeMode={themeMode} />
          )}

          {viewMode === "index_allocation" && (
            <IndexAllocationView
              themeMode={themeMode}
              chartDataList={chartDataList}
              selectedItemCodes={selectedItemCodes}
            />
          )}
        </>
      )}
    </Box>
  );
}
