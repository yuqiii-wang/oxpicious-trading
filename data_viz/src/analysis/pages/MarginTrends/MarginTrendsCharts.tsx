/**
 * MarginTrendsCharts — the 2-plot single-industry margin trends view.
 *
 * Layout (top → bottom):
 *   1. Margin trends — one line per security (indices or ETFs) in the
 *      industry. Toggle Balance | Buy. Selected securities (for the 2nd
 *      plot) are highlighted; the rest render as muted background lines.
 *   2. Pairwise correlation — one line per selected security pair, read
 *      from analysis.margin_industry_correlation (precomputed). Window
 *      toggle 5/20/60/120/255d. Requires ≥2 securities selected.
 *
 * Controls:
 *   • Attribution toggle: Index | ETF (drives both plots; reloads series)
 *   • Series toggle: Balance | Buy (1st plot value + 2nd plot corr column)
 *   • Window toggle: 5d | 20d | 60d | 120d | 255d — sits beside the 2nd plot
 *     heading since it only affects correlation
 *   • Security multi-select (Autocomplete): pick ≥2 securities for 2nd plot
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Chip,
  CircularProgress,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import EChart from "@/components/EChart";
import {
  fetchMarginIndustrySeries,
  fetchMarginIndustryCorrelation,
  fetchMarginTrends,
} from "@/lib/api-client";
import type {
  MarginIndustrySeriesResponse,
  MarginIndustryCorrelationResponse,
  MarginTrendsShadeResponse,
  MarginSecurity,
} from "@shared/types";
import type { EChartsOption } from "echarts";
import {
  GROUP_MAJOR_COLORS,
  MUTED_PALETTE,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { MarginTrendsChartsProps } from "./types";
import {
  CORR_WINDOWS,
  SERIES_OPTIONS,
} from "./constants";
import type { MarginAttribution, MarginSeries, CorrWindow } from "./constants";

const MUTED_GRAY = MUTED_PALETTE[7] ?? "#999999";

export function MarginTrendsCharts({ industryId, themeMode, attribution }: MarginTrendsChartsProps) {
  // ---- Data state ----
  const [seriesData, setSeriesData] = useState<MarginIndustrySeriesResponse | null>(null);
  const [corrData, setCorrData] = useState<MarginIndustryCorrelationResponse | null>(null);
  const [trendsData, setTrendsData] = useState<MarginTrendsShadeResponse | null>(null);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [loadingCorr, setLoadingCorr] = useState(false);
  const [errorSeries, setErrorSeries] = useState<string | null>(null);
  const [errorCorr, setErrorCorr] = useState<string | null>(null);

  // ---- Toggle state (attribution is owned by the parent page) ----
  const [series, setSeries] = useState<MarginSeries>("balance");
  const [corrWindow, setCorrWindow] = useState<CorrWindow>(60);
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);

  const c = axisColors(themeMode);

  // ---- Load series data (1st plot) when industry / attribution changes ----
  useEffect(() => {
    if (!industryId) {
      setSeriesData(null);
      setSelectedCodes([]);
      return;
    }
    let cancelled = false;
    setLoadingSeries(true);
    setErrorSeries(null);
    setCorrData(null);
    fetchMarginIndustrySeries(industryId, attribution)
      .then((resp) => {
        if (cancelled) return;
        setSeriesData(resp);
        setLoadingSeries(false);
        // Default selection: top 2 securities by latest non-null value for
        // the current series, so the 2nd plot has a meaningful default.
        const codeLatest = new Map<string, number>();
        const seen = new Set<string>();
        for (let i = resp.rows.length - 1; i >= 0; i--) {
          const r = resp.rows[i];
          if (seen.has(r.code)) continue;
          const v = series === "balance" ? r.balance : r.buy;
          if (v != null && Number.isFinite(v)) {
            codeLatest.set(r.code, v);
            seen.add(r.code);
          }
        }
        const top2 = resp.securities
          .map((s) => ({ code: s.code, v: codeLatest.get(s.code) ?? -Infinity }))
          .sort((a, b) => b.v - a.v)
          .slice(0, 2)
          .map((x) => x.code);
        setSelectedCodes(top2);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setErrorSeries(e.message);
        setLoadingSeries(false);
        setSelectedCodes([]);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industryId, attribution]);

  // ---- Load trend episodes (1st plot shade overlay) when industry / attribution changes ----
  useEffect(() => {
    if (!industryId) {
      setTrendsData(null);
      return;
    }
    let cancelled = false;
    fetchMarginTrends(industryId, attribution)
      .then((resp) => { if (!cancelled) setTrendsData(resp); })
      .catch(() => { if (!cancelled) setTrendsData(null); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industryId, attribution]);

  // ---- Load correlation data (2nd plot) when selection / series / window changes ----
  useEffect(() => {
    if (!industryId || selectedCodes.length < 2) {
      setCorrData(null);
      return;
    }
    let cancelled = false;
    setLoadingCorr(true);
    setErrorCorr(null);
    fetchMarginIndustryCorrelation(industryId, attribution, selectedCodes, series, corrWindow)
      .then((resp) => {
        if (cancelled) return;
        setCorrData(resp);
        setLoadingCorr(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setErrorCorr(e.message);
        setLoadingCorr(false);
      });
    return () => { cancelled = true; };
  }, [industryId, attribution, selectedCodes, series, corrWindow]);

  // ---- Pivot series data for 1st plot (margin) + close price panel ----
  const seriesPivot = useMemo(() => {
    if (!seriesData || seriesData.rows.length === 0) return null;
    const dateSet = new Set<string>();
    const codeMargin = new Map<string, Map<string, number | null>>();
    const codeClose = new Map<string, Map<string, number | null>>();
    for (const r of seriesData.rows) {
      dateSet.add(r.date);
      const v = series === "balance" ? r.balance : r.buy;
      if (!codeMargin.has(r.code)) codeMargin.set(r.code, new Map());
      codeMargin.get(r.code)!.set(r.date, v);
      if (!codeClose.has(r.code)) codeClose.set(r.code, new Map());
      codeClose.get(r.code)!.set(r.date, r.close);
    }
    const dates = Array.from(dateSet).sort();
    return { dates, codeMargin, codeClose };
  }, [seriesData, series]);

  // ---- 1st plot: margin trends (top grid) + close price (bottom grid) ----
  //  Two grids in ONE EChart with axisPointer.link so hovering on either
  //  panel shows a synced vertical crosshair + tooltip with values from
  //  BOTH panels at the same date.
  const trendsOption = useMemo<EChartsOption | null>(() => {
    if (!seriesData || !seriesPivot || seriesData.securities.length === 0) return null;
    const { dates, codeMargin, codeClose } = seriesPivot;
    const selectedSet = new Set(selectedCodes);
    const seriesLabel = series === "balance" ? "融资余额" : "融资买入额";
    const unit = attribution === "index" ? "亿 (weighted-avg)" : "亿";

    // Build a map of code → trend episodes for markArea shades.
    // Each episode carries its direction (is_trend_up_not_down) so the
    // shade can be colored green (UP) or red (DOWN).
    const trendsByCode = new Map<string, Array<{ start: string; end: string; isUp: boolean }>>();
    if (trendsData) {
      for (const ep of trendsData.episodes) {
        if (!trendsByCode.has(ep.code)) trendsByCode.set(ep.code, []);
        trendsByCode.get(ep.code)!.push({
          start: ep.start_date,
          end: ep.end_date,
          isUp: ep.is_trend_up_not_down,
        });
      }
    }
    // Shade opacity — directional: green for UP trends, red for DOWN.
    // Higher than the old single-color yellow (0.12) since green/red
    // shades need more opacity to be visually distinguishable.
    const trendShadeOpacity = themeMode === "dark" ? 0.18 : 0.20;

    // Build a markArea object for a list of trend episodes. Each episode
    // gets its own itemStyle color (UP_COLOR green / DOWN_COLOR red).
    const buildTrendMarkArea = (
      epList: Array<{ start: string; end: string; isUp: boolean }>,
    ) => ({
      silent: true,
      itemStyle: { borderWidth: 0 },
      data: epList.map((ep) => [
        {
          xAxis: ep.start,
          itemStyle: { color: ep.isUp ? UP_COLOR : DOWN_COLOR, opacity: trendShadeOpacity },
        },
        { xAxis: ep.end },
      ]),
    });

    // code → label map for tooltip trend section.
    const codeToLabel = new Map<string, string>();
    for (const sec of seriesData.securities) {
      codeToLabel.set(sec.code, sec.label || sec.code);
    }

    // ---- Top grid: margin series (all securities; selected = bold) ----
    const marginSeries = seriesData.securities.map((sec, idx) => {
      const dm = codeMargin.get(sec.code);
      const data = dates.map((d) => {
        const v = dm?.get(d) ?? null;
        return v != null && Number.isFinite(v) ? v / 1e8 : null;
      });
      const isSelected = selectedSet.has(sec.code);
      const color = GROUP_MAJOR_COLORS[idx % GROUP_MAJOR_COLORS.length];
      // markArea: directional shade over detected trend episodes.
      // Green (UP_COLOR) for accumulating trends, red (DOWN_COLOR) for
      // unwinding trends. Only attach to SELECTED securities.
      const epList = isSelected ? trendsByCode.get(sec.code) : undefined;
      const markArea = epList && epList.length > 0
        ? buildTrendMarkArea(epList)
        : undefined;
      return {
        name: sec.label || sec.code,
        type: "line" as const,
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data,
        lineStyle: {
          width: isSelected ? 2.0 : 0.8,
          color: isSelected ? color : MUTED_GRAY,
          opacity: isSelected ? 1.0 : 0.25,
        },
        itemStyle: { color },
        emphasis: { focus: "series" as const },
        z: isSelected ? 5 : 1,
        ...(markArea ? { markArea } : {}),
      };
    });

    // ---- Bottom grid: close price series (SELECTED securities only) ----
    //  Close series names are suffixed with " · Close" so the tooltip
    //  formatter can split them from margin series. The legend only
    //  lists margin series (close follows the same selection).
    //  The SAME trend markArea shades are attached to the close series
    //  so the directional shade stretches across BOTH grids vertically.
    const closeSeries = seriesData.securities
      .filter((sec) => selectedSet.has(sec.code))
      .map((sec) => {
        const idx = seriesData.securities.indexOf(sec);
        const dc = codeClose.get(sec.code);
        const data = dates.map((d) => {
          const v = dc?.get(d) ?? null;
          return v != null && Number.isFinite(v) ? v : null;
        });
        const color = GROUP_MAJOR_COLORS[idx % GROUP_MAJOR_COLORS.length];
        // Attach the same trend shades as the margin series so the
        // green/red shade spans both the margin and price plots.
        const epList = trendsByCode.get(sec.code);
        const markArea = epList && epList.length > 0
          ? buildTrendMarkArea(epList)
          : undefined;
        return {
          name: `${sec.label || sec.code} · Close`,
          type: "line" as const,
          xAxisIndex: 1,
          yAxisIndex: 1,
          smooth: false,
          showSymbol: false,
          connectNulls: false,
          data,
          lineStyle: { width: 1.6, color },
          itemStyle: { color },
          z: 3,
          ...(markArea ? { markArea } : {}),
        };
      });

    const CLOSE_SUFFIX = " · Close";

    return {
      backgroundColor: "transparent",
      animation: false,
      // Two grids stacked vertically, sharing the same date range.
      grid: [
        // Top grid (margin) — occupies upper ~58% of the chart height.
        { left: 64, right: 24, top: 32, height: "46%" },
        // Bottom grid (close) — occupies lower ~42%, with x-axis labels.
        { left: 64, right: 24, top: "58%", bottom: 28 },
      ],
      // Sync the axisPointer across both grids so hovering on one panel
      // shows the vertical crosshair on both, and the tooltip collects
      // values from ALL series (margin + close) at the hovered date.
      axisPointer: {
        link: [{ xAxisIndex: "all" }],
      },
      legend: {
        // Only show margin series in the legend (close follows selection).
        data: seriesData.securities
          .filter((s) => selectedSet.has(s.code))
          .map((s) => s.label || s.code),
        textStyle: { color: c.textColor, fontSize: 10 },
        top: 0,
        type: "scroll",
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = (Array.isArray(params) ? params : [params]) as Array<{
            dataIndex?: number;
            seriesName?: string;
            value?: number | null;
            color?: string;
          }>;
          if (arr.length === 0) return "";
          const idx = arr[0].dataIndex ?? 0;
          const dateStr = dates[idx] ?? "";
          // Split into margin and close sections.
          const marginRows = arr
            .filter((p) => p.seriesName && !p.seriesName.endsWith(CLOSE_SUFFIX))
            .filter((p) => p.value != null && Number.isFinite(p.value as number))
            .sort((a, b) => (b.value as number) - (a.value as number))
            .slice(0, 8);
          const closeRows = arr
            .filter((p) => p.seriesName && p.seriesName.endsWith(CLOSE_SUFFIX))
            .filter((p) => p.value != null && Number.isFinite(p.value as number))
            .sort((a, b) => (b.value as number) - (a.value as number));
          const marginHtml = marginRows
            .map((p) => {
              const v = p.value as number;
              return `<div><span style="color:${p.color ?? ""}">●</span> ${p.seriesName}: <b>${fmtNum(v, 2)}</b> ${unit}</div>`;
            })
            .join("");
          const closeHtml = closeRows
            .map((p) => {
              const v = p.value as number;
              const label = (p.seriesName ?? "").replace(CLOSE_SUFFIX, "");
              return `<div><span style="color:${p.color ?? ""}">●</span> ${label}: <b>${fmtNum(v, 2)}</b></div>`;
            })
            .join("");
          // Find active trend episodes at the hovered date for selected codes.
          // Date strings are YYYY-MM-DD so lexicographic comparison is valid.
          const trendRows: Array<{ label: string; isUp: boolean; start: string; end: string }> = [];
          for (const code of selectedCodes) {
            const eps = trendsByCode.get(code);
            if (!eps) continue;
            for (const ep of eps) {
              if (dateStr >= ep.start && dateStr <= ep.end) {
                trendRows.push({
                  label: codeToLabel.get(code) ?? code,
                  isUp: ep.isUp,
                  start: ep.start,
                  end: ep.end,
                });
              }
            }
          }
          const trendHtml = trendRows.length > 0
            ? trendRows.map((t) => {
                const arrow = t.isUp ? "▲" : "▼";
                const dirLabel = t.isUp ? "UP" : "DOWN";
                const color = t.isUp ? UP_COLOR : DOWN_COLOR;
                return `<div><span style="color:${color}">${arrow}</span> ${t.label}: <b>${dirLabel}</b> <span style="opacity:0.7">(${t.start} → ${t.end})</span></div>`;
              }).join("")
            : "";
          const sep1 = marginHtml && closeHtml
            ? `<div style="border-top:1px solid ${c.splitLineColor};margin:3px 0"></div>`
            : "";
          const sep2 = trendHtml && (marginHtml || closeHtml)
            ? `<div style="border-top:1px solid ${c.splitLineColor};margin:3px 0"></div>`
            : "";
          const trendHeader = trendHtml
            ? `<div style="opacity:0.8;font-size:10px">Margin Trend</div>`
            : "";
          return `<div style="font-weight:600">${dateStr}</div>${marginHtml}${sep1}${closeHtml}${sep2}${trendHeader}${trendHtml}`;
        },
      },
      xAxis: [
        // Top grid x-axis (hidden — no labels, no line; shares dates).
        {
          type: "category",
          data: dates,
          gridIndex: 0,
          axisLine: { show: false },
          axisLabel: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
        },
        // Bottom grid x-axis (visible — shows YYYY-MM labels).
        {
          type: "category",
          data: dates,
          gridIndex: 1,
          axisLine: { lineStyle: { color: c.axisLineColor } },
          axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: string) => v.slice(0, 7) },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        // Top grid y-axis (margin values in 亿).
        {
          type: "value",
          gridIndex: 0,
          name: `${seriesLabel} (${unit})`,
          nameTextStyle: { color: c.textColor, fontSize: 9 },
          axisLine: { lineStyle: { color: c.axisLineColor } },
          axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 0) },
          splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
        },
        // Bottom grid y-axis (close price).
        {
          type: "value",
          gridIndex: 1,
          scale: true,
          name: "Close",
          nameTextStyle: { color: c.textColor, fontSize: 9 },
          axisLine: { lineStyle: { color: c.axisLineColor } },
          axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 0) },
          splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
        },
      ],
      series: [...marginSeries, ...closeSeries],
    };
  }, [seriesData, seriesPivot, selectedCodes, attribution, series, themeMode, trendsData, c]);

  // ---- 2nd plot: pairwise correlation curves ----
  const corrOption = useMemo<EChartsOption | null>(() => {
    if (!corrData || corrData.rows.length === 0 || corrData.pairs.length === 0) return null;
    // Build date set + per-pair date→corr map.
    const dateSet = new Set<string>();
    const pairMap = new Map<string, Map<string, number | null>>();
    const labelOf = new Map<string, string>();
    if (seriesData) {
      for (const s of seriesData.securities) labelOf.set(s.code, s.label || s.code);
    }
    for (const r of corrData.rows) {
      dateSet.add(r.date);
      const key = `${r.security_code}|${r.benchmark_code}`;
      if (!pairMap.has(key)) pairMap.set(key, new Map());
      pairMap.get(key)!.set(r.date, r.corr);
    }
    const dates = Array.from(dateSet).sort();

    const seriesLabel = series === "balance" ? "融资余额" : "融资买入额";
    const echartsSeries = corrData.pairs.map((pair, idx) => {
      const key = `${pair.security_code}|${pair.benchmark_code}`;
      const dm = pairMap.get(key);
      const data = dates.map((d) => dm?.get(d) ?? null);
      const aLabel = labelOf.get(pair.security_code) ?? pair.security_code;
      const bLabel = labelOf.get(pair.benchmark_code) ?? pair.benchmark_code;
      const color = GROUP_MAJOR_COLORS[idx % GROUP_MAJOR_COLORS.length];
      return {
        name: `${aLabel} vs ${bLabel}`,
        type: "line" as const,
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data,
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        z: 3,
      };
    });

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
      legend: {
        data: echartsSeries.map((s) => s.name),
        textStyle: { color: c.textColor, fontSize: 10 },
        top: 0,
        type: "scroll",
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = (Array.isArray(params) ? params : [params]) as Array<{
            dataIndex?: number;
            seriesName?: string;
            value?: number | null;
            color?: string;
          }>;
          if (arr.length === 0) return "";
          const idx = arr[0].dataIndex ?? 0;
          const dateStr = dates[idx] ?? "";
          const rowsHtml = arr
            .filter((p) => p.value != null && Number.isFinite(p.value as number))
            .map((p) => {
              const v = p.value as number;
              const valStr = (v >= 0 ? "+" : "") + fmtNum(v, 3);
              return `<div><span style="color:${p.color ?? ""}">●</span> ${p.seriesName ?? ""}: <b>${valStr}</b></div>`;
            })
            .join("");
          return `<div style="font-weight:600">${dateStr}</div>${rowsHtml}`;
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: string) => v.slice(0, 7) },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: -1,
        max: 1,
        name: "Correlation",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 2) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      series: echartsSeries,
    };
  }, [corrData, seriesData, series, themeMode, c]);

  // ---- Autocomplete value (MarginSecurity objects) ----
  const selectedSecs: MarginSecurity[] = useMemo(() => {
    if (!seriesData) return [];
    return selectedCodes
      .map((code) => seriesData.securities.find((s) => s.code === code))
      .filter((s): s is MarginSecurity => s != null);
  }, [selectedCodes, seriesData]);

  return (
    <Box>
      {/* ---- Controls ---- */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
          mb: 1,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            value={series}
            exclusive
            size="small"
            onChange={(_, v: MarginSeries | null) => v && setSeries(v)}
          >
            {SERIES_OPTIONS.map((s) => (
              <ToggleButton key={s} value={s}>
                {s === "balance" ? "Balance" : "Buy"}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        {/* Security multi-select */}
        {seriesData && seriesData.securities.length > 0 && (
          <Autocomplete
            multiple
            size="small"
            sx={{ minWidth: 320, maxWidth: 480, flexGrow: 1 }}
            options={seriesData.securities}
            value={selectedSecs}
            getOptionLabel={(opt) => `${opt.label} (${opt.code})`}
            isOptionEqualToValue={(opt, val) => opt.code === val.code}
            onChange={(_, val: MarginSecurity[]) => {
              setSelectedCodes(val.map((s) => s.code));
            }}
            renderTags={(value, getTagProps) =>
              value.map((opt, idx) => {
                const tagProps = getTagProps({ index: idx });
                return (
                  <Chip
                    {...tagProps}
                    label={opt.label || opt.code}
                    size="small"
                    color={selectedCodes.length >= 2 ? "primary" : "default"}
                  />
                );
              })
            }
            renderInput={(params) => (
              <TextField
                {...params}
                placeholder={
                  selectedCodes.length === 0
                    ? "Select ≥2 securities for correlation"
                    : selectedCodes.length < 2
                      ? `Select ${2 - selectedCodes.length} more for correlation`
                      : "Add or remove securities"
                }
              />
            )}
          />
        )}
      </Box>

      {/* ---- Errors ---- */}
      {errorSeries && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load series: {errorSeries}
        </Alert>
      )}
      {errorCorr && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load correlation: {errorCorr}
        </Alert>
      )}

      {/* ---- 1st plot: margin trends ---- */}
      {loadingSeries && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {!loadingSeries && seriesData && seriesData.rows.length > 0 && (
        <Stack spacing={1.5}>
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              {seriesData.industry_label} — {attribution === "index" ? "Index" : "ETF"}{" "}
              RONGZI {series === "balance" ? "Balance (融资余额)" : "Buy (融资买入额)"}{" "}
              + Close Price
              {selectedCodes.length > 0 && (
                <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                  ({seriesData.securities.length} securities; {selectedCodes.length} selected)
                </Typography>
              )}
            </Typography>
            {trendsOption && <EChart option={trendsOption} height={460} />}
          </Box>

          {/* ---- 2nd plot: pairwise correlation ---- */}
          <Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5, flexWrap: "wrap" }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Pairwise {series === "balance" ? "Balance" : "Buy"} Correlation ({corrWindow}d)
                {corrData && corrData.pairs.length > 0 && (
                  <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                    ({corrData.pairs.length} pairs)
                  </Typography>
                )}
              </Typography>
              <ToggleButtonGroup
                value={corrWindow}
                exclusive
                size="small"
                onChange={(_, v: CorrWindow | null) => v && setCorrWindow(v)}
              >
                {CORR_WINDOWS.map((w) => (
                  <ToggleButton key={w} value={w}>{w}d</ToggleButton>
                ))}
              </ToggleButtonGroup>
            </Box>
            {selectedCodes.length < 2 ? (
              <Tooltip title="Correlation needs at least 2 selected securities.">
                <Alert severity="info" sx={{ py: 0.5 }}>
                  Select at least 2 securities above to see their pairwise correlation.
                </Alert>
              </Tooltip>
            ) : loadingCorr ? (
              <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
                <CircularProgress size={28} />
              </Box>
            ) : corrData && corrData.rows.length === 0 ? (
              <Alert severity="warning" sx={{ py: 0.5 }}>
                No correlation rows for the selected securities under {attribution} attribution.
                Try a different attribution or security set.
              </Alert>
            ) : (
              corrOption && <EChart option={corrOption} height={300} />
            )}
          </Box>
        </Stack>
      )}

      {!loadingSeries && !errorSeries && seriesData && seriesData.rows.length === 0 && (
        <Alert severity="warning">
          No margin data for industry "{industryId}" under {attribution} attribution.
          Run the Python build script (analyze.margins) to populate.
        </Alert>
      )}
    </Box>
  );
}
