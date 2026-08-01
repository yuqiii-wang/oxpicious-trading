/**
 * Capital Flow analysis page (Industry × Broad-Market Benchmark).
 *
 * Captures each industry's "trending popularity" after stripping the
 * dilution caused by broad-market ETFs that share overlapping stock
 * holdings with the industry. The pure metrics are:
 *   • pure_flow         = I * (1 - w_i * O_b / (O_b + O_i))
 *   • pure_growth       = g_i - w_i * g_b
 *   • pure_popularity   = pure_flow * pure_growth
 *   • observed_popularity = I * g_i (no removal, for comparison)
 *   • popularity_retention = pure / observed
 *
 * Layout (refactored to mirror Sec Allocation Perf Attribution):
 *   • Header — title + Refresh button.
 *   • ThemeSelector — shared exchange/sector/industry nav bar. The selectable
 *     unit is an INDUSTRY (analysis.capital_flow.industry_id). Picking an
 *     industry drives the plots below. (Exchange row is cosmetic here —
 *     industries are not exchange-specific — but the component is reused
 *     as-is for consistency across pages.)
 *   • Controls — chart mode toggle (Popularity / Returns / Retention) +
 *     "Broad-market reduction" switch. When the switch is ON (default) the
 *     pure (broad-market-stripped) series are plotted; when OFF the raw
 *     observed series are plotted instead. Retention mode is only available
 *     with reduction ON (it is a pure/observed ratio).
 *   • Benchmark chips — one chip per broad-market benchmark paired with the
 *     selected industry (from /capital-flow/benchmarks). Clicking a chip
 *     toggles whether that benchmark has a plot. By default only the two
 *     primary broad-market benchmarks — 沪深300 (000300) and 中证1000 (000852)
 *     — are selected, so a user who just onboards the page sees exactly two
 *     plots (not the full benchmark set).
 *   • Plots — one time-series chart per selected benchmark, for the selected
 *     industry. Each plot has its own date-range slider.
 *
 * API refactor: the chart endpoint accepts a comma-separated `benchmark_codes`
 * list, so only the benchmarks the user actually plots are fetched (one round
 * trip), instead of loading every benchmark for the industry.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  IconButton,
  Slider,
  Stack,
  Switch,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  FormControlLabel,
} from "@mui/material";
import { ArrowBack, Close } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import ThemeSelector from "@/components/ThemeSelector";
import { useStore } from "@/store/filters";
import { fmtNum, fmtYi } from "@/lib/series";
import {
  UP_COLOR,
  DOWN_COLOR,
  MUTED_PALETTE,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import {
  fetchCapitalFlowThemes,
  fetchCapitalFlowBenchmarks,
  fetchCapitalFlowCharts,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  CapitalFlowBenchmarksResponse,
  CapitalFlowChartResponse,
  CapitalFlowChartRow,
  SectorNode,
} from "../../../shared/types";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";

// All broad-market benchmarks are selected by default so the user sees the
// full broad-market reduction effect for the selected industry. The user can
// then deselect benchmarks they don't need.
const DEFAULT_BENCHMARK_CODES: string[] = []; // empty → signals "select all"

type TimeSeriesMode = "popularity" | "returns" | "retention";

// ----------------------------------------------------------------------------
//  Chart: Time-series for one (industry, benchmark) pair.
//  Renders three coordinated views on a shared x-axis (date), chosen by `mode`:
//    1. Popularity (left y-axis):
//         reduction ON  → pure_popularity bars (signed green/red) with
//                         observed_popularity overlaid as a translucent gray
//                         backdrop line.
//         reduction OFF → observed_popularity bars (I·g_i, signed green/red).
//    2. Returns (right y-axis, %):
//         reduction ON  → pure_growth bars (g_i − w_i·g_b) + industry_return
//                         line (g_i) + benchmark_return line (g_b).
//         reduction OFF → industry_return bars (g_i) + benchmark_return line.
//    3. Retention (right y-axis, %): pure / observed ratio. Only available
//       with reduction ON (it is a pure/observed ratio).
//
//  Tooltip surfaces all underlying quantities (I, B, w_i, w_b, O_i, O_b, g_i,
//  g_b, pure_flow, pure_growth, retention) regardless of the reduction switch,
//  so the user always sees the full picture.
// ----------------------------------------------------------------------------
function buildTimeSeriesOption(
  data: CapitalFlowChartResponse,
  themeMode: ThemeMode,
  mode: TimeSeriesMode,
  reduction: boolean,
): EChartsOption {
  const c = axisColors(themeMode);
  const rows = data.rows;
  const dates = rows.map((r) => r.date);
  const purePops = rows.map((r) => r.pure_popularity);
  const obsPops = rows.map((r) => r.observed_popularity);
  const pureGrowths = rows.map((r) =>
    r.pure_growth == null ? null : r.pure_growth * 100,
  );
  const benchmarkReturns = rows.map((r) =>
    r.benchmark_return == null ? null : r.benchmark_return * 100,
  );
  const industryReturns = rows.map((r) =>
    r.industry_return == null ? null : r.industry_return * 100,
  );
  const retentions = rows.map((r) =>
    r.popularity_retention == null ? null : r.popularity_retention * 100,
  );

  let yAxis: EChartsOption["yAxis"];
  let series: EChartsOption["series"];

  if (mode === "popularity") {
    yAxis = [
      {
        type: "value",
        name: "Popularity",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 2),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ];
    if (reduction) {
      // pure_popularity bars + observed backdrop line.
      series = [
        {
          name: "Pure Popularity",
          type: "bar",
          yAxisIndex: 0,
          data: purePops.map((v) => ({
            value: v,
            itemStyle: { color: v == null ? c.axisLineColor : v >= 0 ? UP_COLOR : DOWN_COLOR },
          })),
          barMaxWidth: 6,
        },
        {
          name: "Observed Popularity",
          type: "line",
          yAxisIndex: 0,
          smooth: false,
          showSymbol: false,
          data: obsPops,
          lineStyle: { width: 1, color: MUTED_PALETTE[5], opacity: 0.5 },
          itemStyle: { color: MUTED_PALETTE[5] },
        },
      ];
    } else {
      // reduction OFF → observed_popularity bars only.
      series = [
        {
          name: "Observed Popularity (I·g_i)",
          type: "bar",
          yAxisIndex: 0,
          data: obsPops.map((v) => ({
            value: v,
            itemStyle: { color: v == null ? c.axisLineColor : v >= 0 ? UP_COLOR : DOWN_COLOR },
          })),
          barMaxWidth: 6,
        },
      ];
    }
  } else if (mode === "returns") {
    yAxis = [
      {
        type: "value",
        name: "Return %",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => (v >= 0 ? "+" : "") + fmtNum(v, 2) + "%",
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ];
    if (reduction) {
      series = [
        {
          name: "Pure Growth (g_i - w_i·g_b)",
          type: "bar",
          yAxisIndex: 0,
          data: pureGrowths.map((v) => ({
            value: v,
            itemStyle: { color: v == null ? c.axisLineColor : v >= 0 ? UP_COLOR : DOWN_COLOR },
          })),
          barMaxWidth: 6,
        },
        {
          name: "Industry Return (g_i)",
          type: "line",
          yAxisIndex: 0,
          smooth: false,
          showSymbol: false,
          data: industryReturns,
          lineStyle: { width: 1.5, color: MUTED_PALETTE[0] },
          itemStyle: { color: MUTED_PALETTE[0] },
        },
        {
          name: "Benchmark Return (g_b)",
          type: "line",
          yAxisIndex: 0,
          smooth: false,
          showSymbol: false,
          data: benchmarkReturns,
          lineStyle: { width: 1, color: MUTED_PALETTE[5], opacity: 0.7 },
          itemStyle: { color: MUTED_PALETTE[5] },
        },
      ];
    } else {
      // reduction OFF → industry_return bars + benchmark_return line.
      series = [
        {
          name: "Industry Return (g_i)",
          type: "bar",
          yAxisIndex: 0,
          data: industryReturns.map((v) => ({
            value: v,
            itemStyle: { color: v == null ? c.axisLineColor : v >= 0 ? UP_COLOR : DOWN_COLOR },
          })),
          barMaxWidth: 6,
        },
        {
          name: "Benchmark Return (g_b)",
          type: "line",
          yAxisIndex: 0,
          smooth: false,
          showSymbol: false,
          data: benchmarkReturns,
          lineStyle: { width: 1.5, color: MUTED_PALETTE[5], opacity: 0.8 },
          itemStyle: { color: MUTED_PALETTE[5] },
        },
      ];
    }
  } else {
    // retention — only meaningful with reduction ON.
    yAxis = [
      {
        type: "value",
        name: "Retention %",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 0) + "%",
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ];
    series = [
      {
        name: "Popularity Retention (pure/observed)",
        type: "line",
        yAxisIndex: 0,
        smooth: false,
        showSymbol: false,
        data: retentions,
        lineStyle: { width: 1.5, color: MUTED_PALETTE[2] },
        itemStyle: { color: MUTED_PALETTE[2] },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: MUTED_PALETTE[5], type: "dashed", opacity: 0.6 },
          data: [{ yAxis: 100, label: { formatter: "100%", fontSize: 9, color: c.textColor } }],
        },
      },
    ];
  }

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 32 }),
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const r: CapitalFlowChartRow | undefined = rows[idx];
        if (!r) return "";
        const ret = r.popularity_retention == null
          ? null
          : r.popularity_retention * 100;
        const fmtPct = (v: number | null) =>
          v == null ? "—" : (v >= 0 ? "+" : "") + fmtNum(v * 100, 4) + "%";
        return `
          <div style="font-weight:600">${r.date}</div>
          <div style="margin-top:2px">I (industry ETF amt): <b style="color:${MUTED_PALETTE[0]}">${fmtYi(r.industry_etf_amount, 2)}</b> <span style="opacity:0.6">(${r.industry_etf_num ?? "—"} ETFs)</span></div>
          <div>B (benchmark ETF amt): <b style="color:${MUTED_PALETTE[1]}">${fmtYi(r.benchmark_etf_amount, 2)}</b> <span style="opacity:0.6">(${r.benchmark_etf_num ?? "—"} ETFs)</span></div>
          <div style="margin-top:2px">w_i = ${fmtNum(r.industry_overlap_weight, 2)}% · w_b = ${fmtNum(r.benchmark_overlap_weight, 2)}%</div>
          <div>O_i = ${fmtYi(r.industry_overlap_amount, 2)} · O_b = ${fmtYi(r.benchmark_overlap_amount, 2)}</div>
          <div style="margin-top:2px">g_i (industry): ${fmtPct(r.industry_return)}</div>
          <div>g_b (benchmark): ${fmtPct(r.benchmark_return)}</div>
          <div style="margin-top:2px">pure_flow: <b>${fmtYi(r.pure_flow, 2)}</b></div>
          <div>pure_growth: <b style="color:${(r.pure_growth ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtPct(r.pure_growth)}</b></div>
          <div>pure_popularity: <b style="color:${(r.pure_popularity ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtNum(r.pure_popularity, 4)}</b></div>
          <div>observed_popularity: ${fmtNum(r.observed_popularity, 4)}</div>
          <div>retention: <b>${ret == null ? "—" : fmtNum(ret, 2) + "%"}</b></div>
        `;
      },
    },
    legend: commonLegend(themeMode, {
      itemWidth: 12,
      itemHeight: 7,
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        // Show ~6 ticks to avoid label crowding on long histories.
        formatter: (v: string) => v.slice(0, 7),
      },
      splitLine: { show: false },
    },
    yAxis,
    series,
  };
}

// ============================================================================
//  Plot — one card per selected benchmark: renders the time-series chart for
//  one (industry, benchmark) pair with its own date-range slider.
// ============================================================================
interface PlotProps {
  data: CapitalFlowChartResponse;
  mode: TimeSeriesMode;
  reduction: boolean;
  themeMode: ThemeMode;
  onRemove: (code: string) => void;
}

function CapitalFlowPlot({ data, mode, reduction, themeMode, onRemove }: PlotProps) {
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Reset slider to full range whenever the plot's data changes.
  useEffect(() => {
    setRange([0, Math.max(0, data.rows.length - 1)]);
  }, [data]);

  const filtered = useMemo<CapitalFlowChartResponse>(() => {
    const [lo, hi] = range;
    return { ...data, rows: data.rows.slice(lo, hi + 1) };
  }, [data, range]);

  const maxIdx = Math.max(0, data.rows.length - 1);
  const subtitle = `${data.industry_label || data.industry_id} vs ${data.benchmark_label || data.benchmark_code}`;

  return (
    <ChartCard
      title={`${data.benchmark_label || data.benchmark_code} · ${data.benchmark_code}`}
      subtitle={subtitle}
      action={
        <IconButton
          size="small"
          aria-label="remove benchmark plot"
          onClick={() => onRemove(data.benchmark_code)}
        >
          <Close fontSize="small" />
        </IconButton>
      }
    >
      {data.rows.length === 0 ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
          <Typography variant="body2" color="text.secondary">
            No capital-flow rows for {data.industry_id} × {data.benchmark_code}.
          </Typography>
        </Box>
      ) : (
        <>
          <EChart
            option={buildTimeSeriesOption(filtered, themeMode, mode, reduction)}
            height={300}
          />
          {maxIdx > 0 && (
            <Box sx={{ px: 1, mt: 0.5 }}>
              <Slider
                value={range}
                onChange={(_, v) => setRange(v as [number, number])}
                min={0}
                max={maxIdx}
                size="small"
                valueLabelDisplay="auto"
                valueLabelFormat={(idx) => data.rows[idx]?.date ?? ""}
                sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
              />
              <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {data.rows[range[0]]?.date ?? "—"}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {data.rows[range[1]]?.date ?? "—"}
                </Typography>
              </Stack>
            </Box>
          )}
        </>
      )}
    </ChartCard>
  );
}

// ============================================================================
//  Page
// ============================================================================
export default function CapitalFlowPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>(null); // cosmetic for industries

  const [benchmarksData, setBenchmarksData] = useState<CapitalFlowBenchmarksResponse | null>(null);
  const [chartsData, setChartsData] = useState<CapitalFlowChartResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Selected benchmark codes (which benchmarks get a plot). Empty until the
  // benchmark list loads; then defaults to ALL broad-market benchmarks.
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [reduction, setReduction] = useState(true);
  const [chartMode, setChartMode] = useState<TimeSeriesMode>("popularity");

  // ---- slug → industry_id lookup, built from the themes tree --------------
  const slugToIndustryId = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sectors) {
      for (const ind of s.industries) {
        m.set(ind.industry_slug, ind.industry_id);
      }
    }
    return m;
  }, [sectors]);

  const selectedIndustryId = industrySlug ? slugToIndustryId.get(industrySlug) ?? null : null;

  // ---- Load themes on mount / refresh -------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchCapitalFlowThemes()
      .then((t) => {
        if (cancelled) return;
        setSectors(t);
        // Auto-select the first sector + first industry (top by n_dates).
        if (t.length > 0 && sectorId == null) {
          setSectorId(t[0].sector_id);
          setIndustrySlug(t[0].industries[0]?.industry_slug ?? null);
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

  // ---- When the selected industry changes: reset benchmark selection to
  //      the defaults (∩ available), load the benchmark chip list + the
  //      default plots' chart data. -----------------------------------------
  useEffect(() => {
    if (!selectedIndustryId) {
      setBenchmarksData(null);
      setChartsData([]);
      return;
    }
    let cancelled = false;
    setBenchmarksData(null);
    setError(null);
    fetchCapitalFlowBenchmarks(selectedIndustryId)
      .then((d) => {
        if (cancelled) return;
        setBenchmarksData(d);
        // Default: select ALL broad-market benchmarks so the user sees the
        // full reduction effect. The user can then deselect ones they don't
        // need.
        setSelectedCodes(d.benchmarks.map((b) => b.benchmark_code));
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndustryId, refreshKey]);

  // ---- Load chart data for the currently selected benchmark codes ---------
  useEffect(() => {
    if (!selectedIndustryId || selectedCodes.length === 0) {
      setChartsData([]);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    fetchCapitalFlowCharts(selectedIndustryId, selectedCodes)
      .then((d) => {
        if (cancelled) return;
        setChartsData(d);
        setChartLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setChartError(e.message);
        setChartLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndustryId, selectedCodes, refreshKey]);

  // ---- Reduction OFF disables retention mode (pure/observed ratio) --------
  useEffect(() => {
    if (!reduction && chartMode === "retention") {
      setChartMode("popularity");
    }
  }, [reduction, chartMode]);

  // ---- Handlers ----------------------------------------------------------
  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/analysis/capital-flow/");
    setRefreshKey((k) => k + 1);
  };

  const handleSectorChange = (id: string | null) => {
    setSectorId(id);
    // Auto-select the first industry of the newly chosen sector.
    const s = sectors.find((x) => x.sector_id === id);
    setIndustrySlug(s?.industries[0]?.industry_slug ?? null);
  };
  const handleIndustryChange = (slug: string | null) => {
    setIndustrySlug(slug);
  };
  const handleExchangeChange = (ex: string | null) => {
    setExchange(ex);
  };

  const toggleBenchmark = useCallback((code: string) => {
    setSelectedCodes((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );
  }, []);

  const removePlot = useCallback((code: string) => {
    setSelectedCodes((prev) => prev.filter((c) => c !== code));
  }, []);

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : "Select a sector";

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
              Capital Flow (Industry × Broad-Market)
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — industry ETF flow with broad-market effect removed.
            Pure metrics strip the overlap-driven spillover from broad-market
            benchmarks (沪深300, 中证1000, …): pure_flow = I·(1 − w_i·O_b/(O_b+O_i)),
            pure_growth = g_i − w_i·g_b, pure_popularity = pure_flow·pure_growth.
            Retention = pure/observed (under 100% means the broad market was
            inflating the industry's apparent popularity). Toggle
            "Broad-market reduction" off to see the raw observed metrics.
            All broad-market benchmarks are selected by default; click a
            benchmark chip to add/remove plots.
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh capital-flow data (bypass cache)"
        />
      </Box>

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        exchange={exchange}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
      />

      {/* Controls: chart mode + broad-market reduction toggle */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
          mb: 1,
        }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={chartMode}
          onChange={(_, v) => { if (v) setChartMode(v as TimeSeriesMode); }}
        >
          <ToggleButton value="popularity" sx={{ px: 1.5, py: 0.25, fontSize: "0.75rem" }}>
            Popularity
          </ToggleButton>
          <ToggleButton value="returns" sx={{ px: 1.5, py: 0.25, fontSize: "0.75rem" }}>
            Returns
          </ToggleButton>
          <ToggleButton
            value="retention"
            disabled={!reduction}
            sx={{ px: 1.5, py: 0.25, fontSize: "0.75rem" }}
          >
            Retention
          </ToggleButton>
        </ToggleButtonGroup>
        <FormControlLabel
          sx={{ mr: 0 }}
          control={
            <Switch
              size="small"
              checked={reduction}
              onChange={(e) => setReduction(e.target.checked)}
            />
          }
          label={
            <Typography variant="caption" color="text.secondary">
              Broad-market reduction {reduction ? "(pure)" : "(observed)"}
            </Typography>
          }
        />
      </Box>

      {/* Benchmark chips — toggle which benchmarks get a plot */}
      {benchmarksData && benchmarksData.benchmarks.length > 0 && (
        <Box
          sx={{
            display: "flex",
            gap: 0.5,
            flexWrap: "wrap",
            alignItems: "center",
            mb: 1.5,
            p: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
            Benchmarks:
          </Typography>
          {benchmarksData.benchmarks.map((b) => {
            const selected = selectedCodes.includes(b.benchmark_code);
            return (
              <Chip
                key={b.benchmark_code}
                label={`${b.benchmark_label || b.benchmark_code} · ${b.benchmark_code}`}
                size="small"
                color={selected ? "primary" : "default"}
                variant={selected ? "filled" : "outlined"}
                onClick={() => toggleBenchmark(b.benchmark_code)}
                sx={{ fontSize: "0.7rem" }}
              />
            );
          })}
        </Box>
      )}

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load capital-flow data: {error}
        </Alert>
      )}
      {!loading && !error && !selectedIndustryId && (
        <Alert severity="warning">Select an industry to see capital-flow plots.</Alert>
      )}
      {!loading && !error && selectedIndustryId && (
        <>
          {chartLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          )}
          {chartError && (
            <Alert severity="error" sx={{ py: 0.5 }}>{chartError}</Alert>
          )}
          {!chartLoading && !chartError && selectedCodes.length === 0 && (
            <Alert severity="warning">
              No benchmarks selected. Click a benchmark chip above to add a plot.
            </Alert>
          )}
          {!chartLoading && !chartError && chartsData.length > 0 && (
            <Stack spacing={1.5}>
              {chartsData
                .filter((d) => selectedCodes.includes(d.benchmark_code))
                .map((d) => (
                  <CapitalFlowPlot
                    key={d.benchmark_code}
                    data={d}
                    mode={chartMode}
                    reduction={reduction}
                    themeMode={themeMode}
                    onRemove={removePlot}
                  />
                ))}
            </Stack>
          )}
        </>
      )}
    </Box>
  );
}
