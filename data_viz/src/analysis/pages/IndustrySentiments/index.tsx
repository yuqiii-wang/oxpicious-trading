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
import ThemeSelector from "@/components/ThemeSelector";
import { useStore } from "@/store/filters";
import {
  fetchIndustrySentimentsThemes,
  fetchIndustrySentimentsChart,
  fetchIndustryAttributionBenchmarks,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  IndustrySentimentsChartResponse,
  IndustrySentimentsIndex,
  IndustryAttributionBenchmarkEntry,
  SectorNode,
} from "../../../../shared/types";
import { IndustrySentimentsPlot } from "./IndustrySentimentsPlot";
import { BenchmarkPriceChart } from "./BenchmarkPriceChart";
import { IndustryBenchmarkAttributionChart } from "./IndustryBenchmarkAttributionChart";
import { IndustryEtfPriceChart } from "./IndustryEtfPriceChart";
import { IndustryEtfContributionChart } from "./IndustryEtfContributionChart";
import { MarketTrendChart } from "./MarketTrendChart";

export default function IndustrySentimentsPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  // Multi-select: list of selected industry slugs. Persists across sector
  // switches so the user can pick industries from multiple sectors.
  const [selectedIndustrySlugs, setSelectedIndustrySlugs] = useState<string[]>([]);
  const [exchange, setExchange] = useState<string | null>(null);

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
  const [viewMode, setViewMode] = useState<"correlation" | "attribution" | "etf_contribution" | "market_trend">("correlation");
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

  // Array of { id, label } for the BenchmarkPriceChart's selectedIndustries
  // prop. Resolves each selected industry_id to its display label.
  const selectedIndustries = useMemo(
    () =>
      selectedIndustryIds.map((id) => {
        const slugEntry = Array.from(slugToIndustryId.entries()).find(
          ([, i]) => i === id,
        );
        const label = slugEntry
          ? (slugToIndustryLabel.get(slugEntry[0]) ?? id)
          : id;
        return { id, label };
      }),
    [selectedIndustryIds, slugToIndustryId, slugToIndustryLabel],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustrySentimentsThemes()
      .then((t) => {
        if (cancelled) return;
        setSectors(t);
        if (t.length > 0 && sectorId == null) {
          setSectorId(t[0].sector_id);
          // Seed the multi-select with the first industry of the first sector
          // so the page shows data immediately on first load.
          const firstSlug = t[0].industries[0]?.industry_slug ?? null;
          setSelectedIndustrySlugs(firstSlug ? [firstSlug] : []);
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
  }, [refreshKey]);

  // Fetch ALL selected industries' chart data in parallel. The dependency is
  // the joined ID string so the effect fires once per selection change.
  const selectedIdsKey = selectedIndustryIds.join(",");
  useEffect(() => {
    if (selectedIndustryIds.length === 0) {
      setChartDataList([]);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    Promise.all(
      selectedIndustryIds.map((id) => fetchIndustrySentimentsChart(id)),
    )
      .then((results) => {
        if (cancelled) return;
        // Re-order results to match the selectedIndustryIds order (Promise.all
        // preserves order, but be defensive in case of any re-ordering).
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
  }, [selectedIdsKey, refreshKey]);

  // Merge multiple industries' chart data into a single
  // IndustrySentimentsChartResponse. When only one industry is selected, the
  // merge is a passthrough (preserves the mean/var overlay). When multiple
  // are selected:
  //   • indices are concatenated (de-duplicated by code — an index may carry
  //     multiple industry tags and would otherwise appear once per tag).
  //   • Each index's name is prefixed with "[industry_short] " so the legend
  //     and tooltip identify which industry it came from.
  //   • aggregation is DROPPED (per-industry mean/var cannot be combined
  //     across industries). The chart hides the mean/var overlay accordingly.
  //   • benchmarks come from the first response (they're identical across
  //     industries — same hardcoded broad-market list).
  //   • industry_label lists all source industries joined by " + ".
  const mergedChartData = useMemo<IndustrySentimentsChartResponse | null>(() => {
    if (chartDataList.length === 0) return null;
    if (chartDataList.length === 1) return chartDataList[0];

    const multi = chartDataList.length > 1;
    const seenCodes = new Set<string>();
    const mergedIndices: IndustrySentimentsIndex[] = [];
    for (const d of chartDataList) {
      // Resolve the short industry label for the prefix. Match by industry_id
      // (the chart response carries industry_id, not slug).
      const slugEntry = Array.from(slugToIndustryId.entries()).find(
        ([, id]) => id === d.industry_id,
      );
      const fullLabel = slugEntry
        ? (slugToIndustryLabel.get(slugEntry[0]) ?? d.industry_label)
        : d.industry_label;
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
    return {
      industry_id: chartDataList.map((d) => d.industry_id).join(","),
      industry_label: chartDataList
        .map((d) => {
          const slugEntry = Array.from(slugToIndustryId.entries()).find(
            ([, id]) => id === d.industry_id,
          );
          return slugEntry
            ? (slugToIndustryLabel.get(slugEntry[0]) ?? d.industry_label)
            : d.industry_label;
        })
        .join(" + "),
      indices: mergedIndices,
      aggregation: [], // dropped — see comment above
      benchmarks: chartDataList[0]?.benchmarks ?? [],
    };
  }, [chartDataList, slugToIndustryId, slugToIndustryLabel]);

  const multiIndustry = chartDataList.length > 1;

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/analysis/industry-sentiments/");
    invalidateCacheForPrefix("/api/analysis/industry-correlations");
    invalidateCacheForPrefix("/api/analysis/industry-benchmark-attribution");
    invalidateCacheForPrefix("/api/analysis/industry-attribution-bars");
    invalidateCacheForPrefix("/api/analysis/industry-etf-contribution");
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
  const handleSectorChange = (id: string | null) => {
    setSectorId(id);
  };
  const handleMultiIndustryChange = (slugs: string[]) => {
    setSelectedIndustrySlugs(slugs);
  };
  // Kept for ThemeSelector's single-select prop signature (no-op in multi mode).
  const handleIndustryChange = (_slug: string | null) => {
    /* no-op — multi-select mode uses handleMultiIndustryChange */
  };
  const handleExchangeChange = (ex: string | null) => {
    setExchange(ex);
  };

  // Header label: when 0 selected → "Select industries"; when 1 → the
  // industry's full sector/industry path; when >1 → "N industries selected".
  const headerLabel =
    selectedIndustrySlugs.length === 0
      ? "Select industries"
      : selectedIndustrySlugs.length === 1
        ? (() => {
            const slug = selectedIndustrySlugs[0];
            const sector = sectors.find((s) =>
              s.industries.some((i) => i.industry_slug === slug),
            );
            const ind = sector?.industries.find((i) => i.industry_slug === slug);
            return ind
              ? `${sector?.sector_label ?? ""} / ${ind.industry_label}`
              : "1 industry selected";
          })()
        : `${selectedIndustrySlugs.length} industries selected`;

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
            sector.
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh industry-sentiments data (bypass cache)"
        />
      </Box>

      <ThemeSelector
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
      {!loading && !error && selectedIndustryIds.length === 0 && (
        <Alert severity="warning">Select one or more industries to see the member indices.</Alert>
      )}
      {!loading && !error && selectedIndustryIds.length > 0 && (
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
              onChange={(_, v: "correlation" | "attribution" | "etf_contribution" | "market_trend" | null) => {
                if (v) setViewMode(v);
              }}
            >
              <ToggleButton value="correlation">Industry Correlation</ToggleButton>
              <ToggleButton value="attribution">Benchmark Attribution</ToggleButton>
              <ToggleButton value="etf_contribution">ETF Contribution</ToggleButton>
              <ToggleButton value="market_trend">Market Trend</ToggleButton>
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
                  selectedIndustryIds={selectedIndustryIds}
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
        </>
      )}
    </Box>
  );
}
