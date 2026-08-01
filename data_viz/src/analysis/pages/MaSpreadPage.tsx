/**
 * MA-Spread analysis page (single-table model, ETF + Index).
 *
 * Layout (right column, top → bottom):
 *   1. One plot — the currently selected pair's chart (two curves + green/red
 *      fill between them). A date-range slider sits above the chart.
 *   2. Pair list — 9 clickable chips (Price/MA5 … MA5/MA255); clicking one
 *      selects the pair shown in the plot above.
 *
 * Left column: security-type toggle (ETF | Index | Stock) + searchable list +
 * date-range slider (unchanged).
 *
 * 9 pairs (canonical order):
 *   Price/MA5, Price/MA20, Price/MA60, Price/MA120, Price/MA255,
 *   MA5/MA20, MA5/MA60, MA5/MA120, MA5/MA255
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  Slider,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack, Search } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import { fmtNum, fmtPct } from "@/lib/series";
import {
  MA120_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  SPOT_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import {
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadChart,
} from "@/lib/api-client";
import type {
  MaSpreadSecType,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MovAveSpreadPairSeries,
} from "../../../shared/types";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";

// Color for the "price" series (ma_short = 0).
const PRICE_COLOR = SPOT_COLOR;

/** Short-series label, e.g. "Price" or "MA5". */
function shortLabel(maShort: number): string {
  return maShort === 0 ? "Price" : `MA${maShort}`;
}

// ============================================================================
//  Chart option builder — two visible lines + green/red fill between them.
// ============================================================================
/**
 * Build the ECharts option for one pair's chart.
 *
 * Implementation: 5 series in one stack ("gapFill"):
 *   1. Visible short line (z=5, no stack)
 *   2. Visible long line  (z=5, no stack)
 *   3. Stack base (invisible): min(short, long)        — stack 'gapFill'
 *   4. Positive delta (green area): max(short - long, 0) — stack 'gapFill'
 *   5. Negative delta (red area):   max(long - short, 0) — stack 'gapFill'
 *
 * When short > long: base=long, pos=short-long, neg=0 → green fill spans
 *   [long, short] = [min, max].
 * When short < long: base=short, pos=0, neg=long-short → red fill spans
 *   [short, long] = [min, max].
 * When short == long: pos=neg=0 → no fill (lines touch).
 */
function buildPairOption(
  pair: MovAveSpreadPairSeries,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;

  const dates = rows.map((r) => r.date);
  const shorts = rows.map((r) => r.short_value);
  const longs = rows.map((r) => r.long_value);
  // slope / curvature arrays for the tooltip. short_slope / short_curvature
  // are populated for every pair — including Price/MA pairs (ma_short = 0),
  // which carry the 1st/2nd derivative of price itself.
  const shortSlopes = rows.map((r) => r.short_slope);
  const shortCurvs = rows.map((r) => r.short_curvature);
  const longSlopes = rows.map((r) => r.long_slope);
  const longCurvs = rows.map((r) => r.long_curvature);

  const baseData: Array<number | null> = new Array(n).fill(null);
  const posData: Array<number | null> = new Array(n).fill(null);
  const negData: Array<number | null> = new Array(n).fill(null);

  for (let i = 0; i < n; i++) {
    const s = shorts[i];
    const l = longs[i];
    if (s == null || l == null) continue;
    const diff = s - l;
    baseData[i] = Math.min(s, l);
    if (diff >= 0) {
      posData[i] = diff;
      negData[i] = 0;
    } else {
      posData[i] = 0;
      negData[i] = -diff;
    }
  }

  const sColor = PRICE_COLOR;
  const lColor = MA120_COLOR;
  const sName = shortLabel(pair.ma_short);
  const lName = `MA${pair.ma_long}`;

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 55, right: 18, bottom: 30 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          seriesName?: string;
          value?: number | Array<number | null> | null;
          marker?: string;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let html = `<div style="font-weight:600;margin-bottom:2px">${dateStr}</div>`;
        const idx = dates.indexOf(dateStr);
        if (idx >= 0) {
          const sv = shorts[idx];
          const lv = longs[idx];
          const gv = sv != null && lv != null && lv !== 0 ? (sv - lv) / lv : null;
          const ss = shortSlopes[idx];
          const sc = shortCurvs[idx];
          const ls = longSlopes[idx];
          const lc = longCurvs[idx];
          html += `<div>${sName}: ${fmtNum(sv)}</div>`;
          html += `<div>${lName}: ${fmtNum(lv)}</div>`;
          html += `<div>gap: ${gv != null ? fmtPct(gv, 3) : "—"}</div>`;
          // slope (1st derivative) + curvature (2nd derivative) of both the
          // short series (price or MA) and the long MA.
          html += `<div style="margin-top:2px;opacity:0.85">${sName} slope: ${fmtNum(ss)} · curv: ${fmtNum(sc)}</div>`;
          html += `<div style="opacity:0.85">${lName} slope: ${fmtNum(ls)} · curv: ${fmtNum(lc)}</div>`;
        }
        return html;
      },
    },
    legend: commonLegend(themeMode, { itemWidth: 12, itemHeight: 7, data: [sName, lName] }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => v.slice(0, 7),
        interval: Math.max(1, Math.floor(n / 6)),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "line",
        name: sName,
        data: shorts,
        symbol: "none",
        lineStyle: { color: sColor, width: 1.4 },
        z: 5,
      },
      {
        type: "line",
        name: lName,
        data: longs,
        symbol: "none",
        lineStyle: { color: lColor, width: 1.4 },
        z: 5,
      },
      {
        type: "line",
        name: "_base",
        data: baseData,
        stack: "gapFill",
        symbol: "none",
        lineStyle: { opacity: 0 },
        z: 1,
      },
      {
        type: "line",
        name: "_pos",
        data: posData,
        stack: "gapFill",
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: UP_COLOR, opacity: 0.4 },
        z: 2,
      },
      {
        type: "line",
        name: "_neg",
        data: negData,
        stack: "gapFill",
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: DOWN_COLOR, opacity: 0.4 },
        z: 2,
      },
    ],
  };
}

// ============================================================================
//  Page
// ============================================================================

export default function MaSpreadPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  // ---- Security-type selector (ETF | Index | Stock) ---------------------
  // Drives every downstream fetch. Toggling resets selectedCode + chart
  // state so stale cross-sec-type data is never shown.
  const [secType, setSecType] = useState<MaSpreadSecType>("etf");

  const [codesData, setCodesData] = useState<MovAveSpreadCodesResponse | null>(null);
  const [codesLoading, setCodesLoading] = useState(false);
  const [codesError, setCodesError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [chartData, setChartData] = useState<MovAveSpreadChartResponse | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  // Which of the 9 pairs is shown in the single plot (default 0 = Price/MA5).
  const [selectedPairIdx, setSelectedPairIdx] = useState(0);

  // Date-range slider state.
  const firstPairRows = chartData?.pairs[0]?.rows ?? [];
  const maxIdx = firstPairRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);

  // Reset slider + pair index when the selected security changes.
  useEffect(() => {
    setRange([0, Math.max(0, (chartData?.pairs[0]?.rows.length ?? 1) - 1)]);
    setSelectedPairIdx(0);
  }, [chartData?.code]);

  // ---- Reset selection state when secType changes ----------------------
  // Wipes the codes list, selected code, and chart so the user
  // never sees stale data from the other sec_type while the new
  // sec_type's codes list is loading.
  useEffect(() => {
    setCodesData(null);
    setCodesError(null);
    setSelectedCode(null);
    setChartData(null);
    setChartError(null);
    setFilterText("");
  }, [secType]);

  // ---- Load the codes list whenever secType changes -------------------
  useEffect(() => {
    setCodesLoading(true);
    setCodesError(null);
    fetchMovAveSpreadCodes(secType)
      .then((d) => {
        setCodesData(d);
        if (d.codes.length > 0) {
          setSelectedCode(d.codes[0].code);
        } else {
          setSelectedCode(null);
        }
      })
      .catch((e: Error) => setCodesError(e.message))
      .finally(() => setCodesLoading(false));
  }, [secType]);

  // ---- Load chart data whenever the selected code or secType changes ---
  useEffect(() => {
    if (!selectedCode) {
      setChartData(null);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    fetchMovAveSpreadChart(selectedCode, secType)
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
  }, [selectedCode, secType]);

  // ---- Filter the codes list by the search box ----------------------------
  const filteredCodes = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (!filterText.trim()) return all;
    const q = filterText.trim().toUpperCase();
    return all.filter(
      (c) => c.code.toUpperCase().includes(q) || c.name.toUpperCase().includes(q),
    );
  }, [codesData, filterText]);

  // Filter each pair's rows to the selected date window.
  const filteredPairs = useMemo(() => {
    if (!chartData) return [];
    return chartData.pairs.map((p) => ({
      ...p,
      rows: p.rows.slice(range[0], range[1] + 1),
    }));
  }, [chartData, range]);

  // Clamp selectedPairIdx to valid range.
  const safePairIdx = Math.min(selectedPairIdx, Math.max(0, filteredPairs.length - 1));
  const selectedPair = filteredPairs[safePairIdx];

  const subtitle = chartData
    ? `${chartData.code} · ${chartData.name || "—"} · ${firstPairRows.length} bars` +
      (firstPairRows.length > 0
        ? ` · ${firstPairRows[0].date} → ${firstPairRows[firstPairRows.length - 1].date}`
        : "")
    : "Loading…";

  const secLabel = secType === "etf" ? "ETFs" : secType === "index" ? "Indices" : "Stocks";

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
        <IconButton
          onClick={() => navigate("/analysis/commons")}
          size="small"
          aria-label="back to commons"
        >
          <ArrowBack />
        </IconButton>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          MA-Spread
        </Typography>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        9 pairs (5 Price/MA + 4 MA5/MA). The plot shows the short + long series
        with green fill when short &gt; long (growth) and red fill when short &lt; long (decline).
        The chart tooltip shows each series' slope (1st derivative) and curvature (2nd derivative) —
        including price's own slope/curvature for Price/MA pairs.
        Toggle the security type (ETF / Index / Stock) in the left panel.
        Stock support is reserved — the list will be empty until
        stock_tech_stats is created and the build script populates stock rows.
      </Typography>

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "300px 1fr" }, gap: 2 }}>
        {/* ---- Left column: security-type toggle + list with search ---------- */}
        <Box sx={{ position: "sticky", top: 72, alignSelf: "start" }}>
          <ChartCard title="Security Type" subtitle="ETF · Index · Stock">
            <ToggleButtonGroup
              value={secType}
              exclusive
              onChange={(_, v: MaSpreadSecType | null) => {
                if (v) setSecType(v);
              }}
              size="small"
              fullWidth
              aria-label="security type"
              sx={{ mb: 1 }}
            >
              <ToggleButton value="etf" sx={{ fontSize: "0.75rem", py: 0.25 }}>
                ETF
              </ToggleButton>
              <ToggleButton value="index" sx={{ fontSize: "0.75rem", py: 0.25 }}>
                Index
              </ToggleButton>
              <ToggleButton value="stock" sx={{ fontSize: "0.75rem", py: 0.25 }}>
                Stock
              </ToggleButton>
            </ToggleButtonGroup>
          </ChartCard>

          <Box sx={{ mt: 1 }}>
            <ChartCard title={secLabel} subtitle={`${filteredCodes.length} codes`}>
              <TextField
                size="small"
                fullWidth
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                placeholder="Filter by code or name"
                sx={{ mb: 1, "& .MuiInputBase-input": { fontSize: "0.8rem" } }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Search fontSize="small" sx={{ color: "text.secondary" }} />
                    </InputAdornment>
                  ),
                }}
              />
              <Box sx={{ maxHeight: "calc(100vh - 240px)", overflowY: "auto" }}>
                {codesLoading && (
                  <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
                    <CircularProgress size={20} />
                  </Box>
                )}
                {codesError && (
                  <Alert severity="error" sx={{ py: 0.5 }}>{codesError}</Alert>
                )}
                {!codesLoading && !codesError && filteredCodes.length === 0 && (
                  <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                    No codes match.
                  </Typography>
                )}
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    px: 1,
                    py: 0.5,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    position: "sticky",
                    top: 0,
                    bgcolor: "background.paper",
                    zIndex: 1,
                  }}
                >
                  <Typography variant="caption" sx={{ fontSize: "0.7rem", fontWeight: 700, color: "text.secondary" }}>
                    Code
                  </Typography>
                  <Typography variant="caption" sx={{ fontSize: "0.7rem", fontWeight: 700, color: "text.secondary" }}>
                    Spread
                  </Typography>
                </Box>
                <Stack spacing={0.25}>
                  {filteredCodes.map((c) => {
                    const active = c.code === selectedCode;
                    const spread = c.max_spread;
                    return (
                      <Box
                        key={c.code}
                        onClick={() => setSelectedCode(c.code)}
                        sx={{
                          cursor: "pointer",
                          px: 1,
                          py: 0.5,
                          borderRadius: 1,
                          bgcolor: active ? "action.selected" : "transparent",
                          "&:hover": { bgcolor: "action.hover" },
                          borderLeft: active ? "3px solid" : "3px solid transparent",
                          borderColor: active ? "primary.main" : "transparent",
                        }}
                      >
                        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                          <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.8rem" }}>
                            {c.code}
                          </Typography>
                          <Typography
                            variant="caption"
                            sx={{
                              fontSize: "0.7rem",
                              color: spread == null ? "text.disabled" : UP_COLOR,
                              fontWeight: 600,
                            }}
                          >
                            {spread == null ? "—" : fmtPct(spread * 100, 1)}
                          </Typography>
                        </Box>
                        <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                          {c.name || "—"} · {c.n_dates}d
                        </Typography>
                      </Box>
                    );
                  })}
                </Stack>
              </Box>
            </ChartCard>
          </Box>
        </Box>

        {/* ---- Right column: chart (top) → pairs (bottom) -- */}
        <Stack spacing={2}>
          {/* ============ 1. One plot (top) ============ */}
          <ChartCard
            title={selectedPair ? selectedPair.pair_label : "Chart"}
            subtitle={subtitle}
          >
            {chartLoading && (
              <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                <CircularProgress size={24} />
              </Box>
            )}
            {chartError && <Alert severity="error" sx={{ mb: 1 }}>{chartError}</Alert>}
            {!chartLoading && !chartError && firstPairRows.length > 0 && maxIdx > 0 && (
              <Box sx={{ px: 1, py: 0.5 }}>
                <Slider
                  value={range}
                  onChange={(_, v) => setRange(v as [number, number])}
                  min={0}
                  max={maxIdx}
                  size="small"
                  valueLabelDisplay="auto"
                  valueLabelFormat={(idx) => firstPairRows[idx]?.date ?? ""}
                  sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
                />
                <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                    {firstPairRows[range[0]]?.date ?? "—"}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                    {firstPairRows[range[1]]?.date ?? "—"}
                  </Typography>
                </Stack>
              </Box>
            )}
            {!chartLoading && !chartError && selectedPair && selectedPair.rows.length > 0 && (
              <EChart option={buildPairOption(selectedPair, themeMode)} height={420} />
            )}
            {!chartLoading && !chartError && selectedPair && selectedPair.rows.length === 0 && (
              <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
                <Typography variant="caption" color="text.secondary">
                  No data for {selectedPair.pair_label} in this date range.
                </Typography>
              </Box>
            )}
          </ChartCard>

          {/* ============ 2. Pair list (bottom) ============ */}
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block", fontSize: "0.7rem" }}>
              Pairs — click to show in plot above
            </Typography>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
              {filteredPairs.map((pair, idx) => {
                const active = idx === safePairIdx;
                return (
                  <Chip
                    key={pair.pair_label}
                    label={pair.pair_label}
                    clickable
                    size="small"
                    color={active ? "primary" : "default"}
                    variant={active ? "filled" : "outlined"}
                    onClick={() => setSelectedPairIdx(idx)}
                    sx={{ fontSize: "0.75rem" }}
                  />
                );
              })}
            </Box>
          </Box>
        </Stack>
      </Box>
    </Box>
  );
}


