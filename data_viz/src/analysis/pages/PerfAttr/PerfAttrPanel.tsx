/**
 * PerfAttrPanel — one card per code: benchmark selector + two time-series
 * charts.
 *
 * Each panel renders:
 *   1. Fluctuation Attribution chart (top) — grouped bars per benchmark
 *      showing shared-weight contribution (= fractional benchmark return ×
 *      composition overlap) and overlap %. Click a bar to select that
 *      benchmark. An All/Sector toggle shows/hides broad-market benchmarks.
 *   2. %/Abs toggle for the time-series charts (shown after a bar is clicked).
 *   3. Index Trading Amt Contribution (benchmark vs subject ETF turnover)
 *   4. Close Price History Trend (subject vs benchmark) with rolling
 *      close correlations (5/20/60/255d) in the tooltip.
 *
 * Returns are NOT stored in the DB — benchmark_return and subject_return
 * are computed on-the-fly in the attribution SQL via LATERAL joins to
 * stats.index_basic_stats (fractional returns, scale-invariant).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Popover,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import * as echarts from "echarts";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import {
  fetchPerfAttrAttribution,
  fetchPerfAttrChart,
} from "@/lib/api-client";
import type {
  PerfAttrAttributionResponse,
  PerfAttrChartResponse,
} from "@shared/types";
import type { PanelProps, ChartMode } from "./types";
import { buildFluctuationOption } from "./fluctuationOption";
import { buildComparisonOption } from "./comparisonOption";
import { buildAmountContributionOption } from "./amountContributionOption";

export function PerfAttrPanel({ code, name, secType, themeMode }: PanelProps) {
  const [data, setData] = useState<PerfAttrAttributionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Selected benchmark for the two time-series charts. Auto-selected when
  // attribution data loads (000300 if available, else first benchmark).
  const [selectedBenchmark, setSelectedBenchmark] = useState<{
    code: string;
    name: string;
  } | null>(null);
  const [chartData, setChartData] = useState<PerfAttrChartResponse | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  // Comparison chart display mode: "percentage" rebases both curves to 0% at
  // the first common date (best for relative-performance trend comparison);
  // "absolute" shows raw close prices on dual y-axes.
  const [chartMode, setChartMode] = useState<ChartMode>("percentage");

  // Broad-market benchmark visibility in the Fluctuation Attribution chart.
  // When FALSE, broad-market benchmarks (沪深300, 上证指数, etc.) are hidden so
  // sector/industry benchmarks stand out.
  const [showBroadMarket, setShowBroadMarket] = useState(true);

  // Popover anchor for the "Linked ETFs" button in the Index Trading Amt
  // contribution chart header. Null when the popover is closed.
  const [etfPopoverAnchor, setEtfPopoverAnchor] = useState<HTMLElement | null>(null);

  // Click handler for the Fluctuation Attribution chart — uses dataIndex
  // from the click params to look up the benchmark code from a ref to the
  // sorted benchmarks array. Ref-based so the chart-level binding (done once
  // via onReady) always reads the latest data without re-binding.
  const dataRef = useRef(data);
  useEffect(() => { dataRef.current = data; }, [data]);
  const showBroadMarketRef = useRef(showBroadMarket);
  useEffect(() => { showBroadMarketRef.current = showBroadMarket; }, [showBroadMarket]);

  const handleFluctuationReady = useCallback((chart: echarts.ECharts) => {
    // Use zr-level (canvas) click to avoid ECharts series-level event
    // binding quirks. Convert pixel → x-axis category index → benchmark code.
    chart.getZr().on("click", (params: { offsetX?: number; offsetY?: number }) => {
      const x = params.offsetX;
      const y = params.offsetY;
      if (x == null || y == null) return;
      // Only fire inside the plot grid.
      if (!chart.containPixel("grid", [x, y])) return;
      const idx = chart.convertFromPixel({ xAxisIndex: 0 }, x);
      const dataIdx = Math.round(idx);
      if (dataIdx < 0) return;
      const d = dataRef.current;
      if (!d) return;
      // Re-derive the sorted+filtered benchmarks the same way
      // buildFluctuationOption does, to map index → benchmark_code.
      let sorted = [...d.benchmarks].sort((a, b) => {
        const ar = a.benchmark_return ?? 0;
        const br = b.benchmark_return ?? 0;
        const aw = a.code_sec_shared_weight ?? 0;
        const bw = b.code_sec_shared_weight ?? 0;
        const aeff = ar * (aw / 100);
        const beff = br * (bw / 100);
        if (aeff >= 0 && beff < 0) return -1;
        if (aeff < 0 && beff >= 0) return 1;
        return beff - aeff;
      });
      if (!showBroadMarketRef.current) {
        sorted = sorted.filter((b) => b.is_broad_market !== true);
      }
      sorted = sorted.filter(
        (b) => b.code_sec_shared_weight != null && b.benchmark_return != null,
      );
      const bench = sorted[dataIdx];
      if (bench) {
        setSelectedBenchmark({
          code: bench.benchmark_code,
          name: bench.benchmark_name || bench.benchmark_code,
        });
      }
    });
  }, []);

  // Memoized Fluctuation Attribution chart option — recomputes only when the
  // attribution data, theme, or broad-market toggle changes. Returns null
  // when data hasn't loaded yet (the chart is only rendered when data is
  // non-null, but useMemo runs on every render regardless).
  const fluctuationOption = useMemo(
    () => (data ? buildFluctuationOption(data, themeMode, showBroadMarket) : null),
    [data, themeMode, showBroadMarket],
  );

  // Fetch attribution data (benchmark list for the selector) on mount.
  // NOTE: no auto-select — the expanded time-series charts are shown ONLY
  // after the user clicks a bar in the Fluctuation Attribution chart.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPerfAttrAttribution(code, secType, null)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  // Reset when the subject code or sec_type changes.
  useEffect(() => {
    setSelectedBenchmark(null);
    setChartData(null);
    setChartError(null);
  }, [code, secType]);

  // Fetch the time-series chart data whenever the selected benchmark changes.
  useEffect(() => {
    if (!selectedBenchmark) {
      setChartData(null);
      setChartError(null);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    fetchPerfAttrChart(code, selectedBenchmark.code, secType)
      .then((d) => {
        if (cancelled) return;
        setChartData(d);
        setChartLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setChartError(e.message);
        setChartLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBenchmark, code, secType]);

  // Wire up cross-chart tooltip sync via echarts.connect() — the two
  // charts share one group so hovering either shows the tooltip on both.
  const chartGroup = selectedBenchmark
    ? `perf-attr-${code}-${selectedBenchmark.code}`
    : null;
  const connectedGroupsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!chartGroup) return;
    if (connectedGroupsRef.current.has(chartGroup)) return;
    echarts.connect(chartGroup);
    connectedGroupsRef.current.add(chartGroup);
  }, [chartGroup]);

  const subtitle = data
    ? `${data.code} · ${data.name || name || "—"} · ${data.latest_date || "—"}`
    : `${code} · ${name || "—"}`;

  return (
    <ChartCard title="Perf Attribution" subtitle={subtitle}>
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={20} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          {error}
        </Alert>
      )}
      {!loading && !error && data && data.benchmarks.length > 0 && (
        <>
          {/* Fluctuation Attribution chart — shared-weight contribution per
              benchmark for the latest date. Bar 1 (left axis) = benchmark
              fractional return × (shared_weight/100); Bar 2 (right axis) =
              shared_weight %. Click a bar to select that benchmark for the
              time-series charts below. */}
          <Box sx={{ mb: 1 }}>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mb: 0.25,
                gap: 1,
              }}
            >
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                Fluctuation Attribution (contribution = return × overlap) · {data.latest_date}
              </Typography>
              <ToggleButtonGroup
                size="small"
                value={showBroadMarket ? "all" : "sector"}
                exclusive
                onChange={(_, v) => { if (v) setShowBroadMarket(v === "all"); }}
                sx={{ flexShrink: 0 }}
              >
                <ToggleButton
                  value="all"
                  sx={{ py: 0, px: 0.75, fontSize: "0.6rem", lineHeight: 1.2 }}
                >
                  All
                </ToggleButton>
                <ToggleButton
                  value="sector"
                  sx={{ py: 0, px: 0.75, fontSize: "0.6rem", lineHeight: 1.2 }}
                >
                  Sector
                </ToggleButton>
              </ToggleButtonGroup>
            </Box>
            <EChart
              option={fluctuationOption ?? {}}
              height={300}
              onReady={handleFluctuationReady}
            />
          </Box>

          {/* Expanded time-series charts — shown ONLY after the user clicks a
              bar in the Fluctuation Attribution chart above. No dropdown; the
              benchmark is selected exclusively via bar click.
              1. Index Trading Amt contribution (benchmark vs subject index ETF
                 turnover) — tooltip surfaces the bench/code liquidity ratio,
                 its 5-day MA, and the subject's share (1/ratio).
              2. Close price history trend (subject vs benchmark) — tooltip
                 surfaces the rolling 5/20/60/255-day close-price correlations.
              The %/Abs toggle applies to both charts: "percentage" rebases
              both curves to 0% at the first common date; "absolute" shows raw
              values (亿元 / close price). */}
          {!selectedBenchmark && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
                Click a bar above to expand the time-series charts for that benchmark.
              </Typography>
            </Box>
          )}
          {selectedBenchmark && chartLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <CircularProgress size={20} />
            </Box>
          )}
          {selectedBenchmark && chartError && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              {chartError}
            </Alert>
          )}
          {selectedBenchmark && !chartLoading && !chartError && chartData && chartData.rows.length > 0 && (
            <>
              <Box sx={{ mt: 1 }}>
                {/* Expanded charts header: selected benchmark label + %/Abs toggle */}
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    mb: 0.25,
                    gap: 1,
                  }}
                >
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                    Selected: <b>{selectedBenchmark.name}</b> ({selectedBenchmark.code})
                  </Typography>
                  <ToggleButtonGroup
                    size="small"
                    value={chartMode}
                    exclusive
                    onChange={(_, v) => {
                      if (v) setChartMode(v as ChartMode);
                    }}
                    sx={{ flexShrink: 0 }}
                  >
                    <ToggleButton
                      value="percentage"
                      sx={{ py: 0, px: 0.75, fontSize: "0.65rem", lineHeight: 1.2 }}
                    >
                      %
                    </ToggleButton>
                    <ToggleButton
                      value="absolute"
                      sx={{ py: 0, px: 0.75, fontSize: "0.65rem", lineHeight: 1.2 }}
                    >
                      Abs
                    </ToggleButton>
                  </ToggleButtonGroup>
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">
                    Index Trading Amt contribution (benchmark vs subject index ETF turnover)
                  </Typography>
                  {/* Linked ETFs button — opens a popover listing the ETFs
                      tracking the benchmark and subject indices. */}
                  <Typography
                    component="span"
                    variant="caption"
                    onClick={(e) => setEtfPopoverAnchor(e.currentTarget)}
                    sx={{
                      cursor: "pointer",
                      color: "primary.main",
                      fontSize: "0.65rem",
                      textDecoration: "underline",
                      ml: 0.5,
                    }}
                  >
                    Linked ETFs
                  </Typography>
                  <Popover
                    open={etfPopoverAnchor != null}
                    anchorEl={etfPopoverAnchor}
                    onClose={() => setEtfPopoverAnchor(null)}
                    anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
                    transformOrigin={{ vertical: "top", horizontal: "left" }}
                    PaperProps={{ sx: { maxWidth: 360, p: 1.25 } }}
                  >
                    {chartData && (
                      <Box sx={{ fontSize: "0.75rem" }}>
                        <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
                          Benchmark: {chartData.benchmark_name || chartData.benchmark_code}
                        </Typography>
                        {chartData.benchmark_linked_etfs.length > 0 ? (
                          <Box component="ul" sx={{ m: 0, pl: 1.5, mb: 1 }}>
                            {chartData.benchmark_linked_etfs.map((etf) => (
                              <li key={etf.code}>
                                {etf.name} <span style={{ opacity: 0.5 }}>({etf.code})</span>
                              </li>
                            ))}
                          </Box>
                        ) : (
                          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1, fontStyle: "italic" }}>
                            No ETF tracks this benchmark index.
                          </Typography>
                        )}
                        <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
                          Subject: {chartData.name || chartData.code}
                        </Typography>
                        {chartData.code_linked_etfs.length > 0 ? (
                          <Box component="ul" sx={{ m: 0, pl: 1.5 }}>
                            {chartData.code_linked_etfs.map((etf) => (
                              <li key={etf.code}>
                                {etf.name} <span style={{ opacity: 0.5 }}>({etf.code})</span>
                              </li>
                            ))}
                          </Box>
                        ) : (
                          <Typography variant="caption" color="text.secondary" sx={{ display: "block", fontStyle: "italic" }}>
                            No ETF tracks this subject index.
                          </Typography>
                        )}
                      </Box>
                    )}
                  </Popover>
                </Box>
                <EChart
                  option={buildAmountContributionOption(chartData, themeMode, chartMode)}
                  height={170}
                  group={`perf-attr-${code}-${selectedBenchmark.code}`}
                />
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, mb: 0.25 }}>
                  Close price history trend (subject vs benchmark)
                </Typography>
                <EChart
                  option={buildComparisonOption(chartData, themeMode, chartMode)}
                  height={200}
                  group={`perf-attr-${code}-${selectedBenchmark.code}`}
                />
              </Box>
            </>
          )}
          {selectedBenchmark && !chartLoading && !chartError && chartData && chartData.rows.length === 0 && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <Typography variant="body2" color="text.secondary">
                No data in the selected date range.
              </Typography>
            </Box>
          )}
        </>
      )}
      {!loading && !error && data && data.benchmarks.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No benchmark data for {code}.
          </Typography>
        </Box>
      )}
    </ChartCard>
  );
}
