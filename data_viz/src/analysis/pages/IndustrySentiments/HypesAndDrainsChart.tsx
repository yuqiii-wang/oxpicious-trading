/**
 * HypesAndDrainsChart — the "Hypes & Drains" sub-view of "Market Trend" mode.
 *
 * Uses SEASONAL (monthly) rankings to determine which industry curves to
 * show. The plot is still daily (benchmark + industry rolling curves are
 * daily), but WHICH industries appear is frozen per month.
 *
 * State machine per industry per date:
 *   ACTIVE  — in the current month's top/bottom 5 → full opacity + shade
 *   FADING  — was ranked in a past month, curve still on same side of
 *             benchmark → very light transparent line, no shade
 *   HIDDEN  — curve crossed the benchmark, or never ranked → not rendered
 *
 * The benchmark is picked via the Autocomplete dropdown in the parent
 * MarketTrendChart (upper right corner). This component only owns the period
 * (5/20/60/120/255/500d, default 120d).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  FormControl,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import EChart from "@/components/EChart";
import { fetchIndustryHypesAndDrains } from "@/lib/api-client";
import type { IndustryHypesAndDrainsResponse, SeasonalRankingRow } from "../../../../shared/types";
import type { HypesAndDrainsChartProps } from "./types";
import { ROLLING_DAYS, ROLLING_DAYS_LABELS, DEFAULT_ROLLING_DAYS } from "./constants";
import { buildHypesAndDrainsOption } from "./hypesAndDrainsOption";

/** Weighting method: 'equal' (raw attribution contribution) or 'amt'
 *  (contribution × shared_trading_amt — absolute yuan impact). */
type Weighting = "equal" | "amt";

/** Format a peak_metric_value for display based on the weighting method.
 *  - equal: show as decimal (e.g. +0.0350)
 *  - amt: show in billions of yuan (e.g. +¥18.8B) */
function formatPeak(v: number | null, weighting: Weighting): string {
  if (v == null) return "—";
  if (weighting === "equal") {
    return (v >= 0 ? "+" : "") + v.toFixed(4);
  }
  // amt: values are in yuan, format as ¥B (billions)
  const absV = Math.abs(v);
  const sign = v >= 0 ? "+" : "−";
  if (absV >= 1e9) return `${sign}¥${(absV / 1e9).toFixed(2)}B`;
  if (absV >= 1e6) return `${sign}¥${(absV / 1e6).toFixed(2)}M`;
  if (absV >= 1e3) return `${sign}¥${(absV / 1e3).toFixed(2)}K`;
  return `${sign}¥${absV.toFixed(2)}`;
}

export function HypesAndDrainsChart({ benchmarkCode, themeMode }: HypesAndDrainsChartProps) {
  const [data, setData] = useState<IndustryHypesAndDrainsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [periodDays, setPeriodDays] = useState<number>(DEFAULT_ROLLING_DAYS);
  const [weighting, setWeighting] = useState<Weighting>("equal");
  // Default: show only top-3 / bottom-3. Toggle to expand to top-5 / bottom-5.
  const [maxRank, setMaxRank] = useState<3 | 5>(3);

  // The date the user clicked on the chart (null → default to the latest
  // season). Used to reflect the chosen month in the detail table below.
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  // Fetch data when benchmarkCode, period, or weighting changes.
  useEffect(() => {
    if (!benchmarkCode) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSelectedDate(null);
    fetchIndustryHypesAndDrains(benchmarkCode, periodDays, weighting)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [benchmarkCode, periodDays, weighting]);

  // Build the chart option. Passes selectedDate so a dashed markLine is
  // drawn at the clicked date for visual feedback.
  const option = useMemo(
    () =>
      data && data.benchmark_series.length > 0
        ? buildHypesAndDrainsOption(data, themeMode, selectedDate, maxRank)
        : null,
    [data, themeMode, selectedDate, maxRank],
  );

  // --- Click handler: native DOM event on the chart container ---
  // The EChart component's onCanvasClick (ZRender) doesn't fire reliably
  // for this chart (likely due to stacked area series intercepting zr
  // events). Instead, we attach a native click listener to the wrapper
  // div and use the ECharts instance (via onReady) to convert pixel
  // coordinates to a data index.
  const chartWrapperRef = useRef<HTMLDivElement | null>(null);
  const [chartInstance, setChartInstance] = useState<
    import("echarts").ECharts | null
  >(null);
  const handleChartReady = useCallback((chart: import("echarts").ECharts) => {
    setChartInstance(chart);
  }, []);

  useEffect(() => {
    const div = chartWrapperRef.current;
    if (!div || !chartInstance || !data) return;

    const handleClick = (e: MouseEvent) => {
      const rect = div.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      // Ignore clicks outside the plot grid.
      if (!chartInstance.containPixel("grid", [x, y])) return;
      const idx = chartInstance.convertFromPixel({ xAxisIndex: 0 }, x);
      const dataIdx = Math.round(idx);
      if (dataIdx < 0 || dataIdx >= data.benchmark_series.length) return;
      setSelectedDate(data.benchmark_series[dataIdx].date);
    };

    div.addEventListener("click", handleClick);
    return () => div.removeEventListener("click", handleClick);
  }, [chartInstance, data]);

  // Derive the selected season's HYPE / DRAIN rankings for the summary
  // table. When no date is clicked, defaults to the latest season.
  const { selectedHypes, selectedDrains, selectedSeason } = useMemo(() => {
    if (!data || data.seasonal_rankings.length === 0) {
      return { selectedHypes: [] as SeasonalRankingRow[], selectedDrains: [] as SeasonalRankingRow[], selectedSeason: "" };
    }
    const sortedSeasons = data.seasons.map((s) => s.season_qkey).sort();
    const latest = sortedSeasons[sortedSeasons.length - 1];
    // If a date was clicked, find the season whose [start, end] range
    // contains it. Falls back to the latest season when the clicked date
    // is outside any ranked season (e.g. before the first ranked month).
    let season = latest;
    if (selectedDate) {
      const match = data.seasons.find(
        (s) => selectedDate >= s.season_start && selectedDate <= s.season_end,
      );
      if (match) season = match.season_qkey;
    }
    const hypes = data.seasonal_rankings
      .filter((r) => r.season_qkey === season && r.rank_side === "HYPE" && r.rank <= maxRank)
      .sort((a, b) => a.rank - b.rank);
    const drains = data.seasonal_rankings
      .filter((r) => r.season_qkey === season && r.rank_side === "DRAIN" && r.rank <= maxRank)
      .sort((a, b) => a.rank - b.rank);
    return { selectedHypes: hypes, selectedDrains: drains, selectedSeason: season };
  }, [data, selectedDate, maxRank]);

  // Build the subtitle.
  const subtitle = useMemo(() => {
    if (!data) return "";
    const parts: string[] = [];
    const wLabel = weighting === "equal" ? "equal-weighted" : "trading-amt-weighted";
    parts.push(`${data.benchmark_name} (${data.benchmark_code}, ${data.period_days}d, ${wLabel})`);
    if (selectedSeason) {
      parts.push(selectedDate ? `Month: ${selectedSeason}` : `Latest: ${selectedSeason}`);
      parts.push(`${data.seasons.length} seasons`);
      parts.push(`${data.industry_series.length} industries tracked`);
    }
    return parts.join(" · ");
  }, [data, selectedSeason, selectedDate, weighting]);

  return (
    <Box>
      {subtitle && (
        <Typography variant="caption" sx={{ fontSize: "0.72rem", color: "text.secondary", display: "block", mb: 0.5 }}>
          {subtitle}
        </Typography>
      )}
      {/* --- Controls: weighting toggle + period dropdown (benchmark is owned by parent) --- */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: 1.5,
          mb: 1,
          flexWrap: "wrap",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Tooltip
            title={
              weighting === "equal"
                ? "Ranking by raw attribution contribution (benchmark return minus non-industry return). Every industry is judged purely by its marginal effect on the benchmark return."
                : "Ranking by contribution × shared trading amount (absolute yuan impact). Industries with more trading activity have more influence on the ranking."
            }
            arrow
          >
            <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600, cursor: "help" }}>
              Weighting:
            </Typography>
          </Tooltip>
          <ToggleButtonGroup
            value={weighting}
            exclusive
            size="small"
            onChange={(_e, v) => { if (v !== null) setWeighting(v); }}
            sx={{ height: 28 }}
          >
            <ToggleButton value="equal" sx={{ px: 1, py: 0.25, fontSize: "0.7rem", textTransform: "none" }}>
              Equal
            </ToggleButton>
            <ToggleButton value="amt" sx={{ px: 1, py: 0.25, fontSize: "0.7rem", textTransform: "none" }}>
              Trading Amt
            </ToggleButton>
          </ToggleButtonGroup>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Tooltip
            title="Number of top HYPE / bottom DRAIN industries to show. Default is 3; expand to 5 to see more ranked industries."
            arrow
          >
            <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600, cursor: "help" }}>
              Show:
            </Typography>
          </Tooltip>
          <ToggleButtonGroup
            value={maxRank}
            exclusive
            size="small"
            onChange={(_e, v) => { if (v !== null) setMaxRank(v as 3 | 5); }}
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
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600 }}>
            Period:
          </Typography>
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <Select
              value={periodDays}
              onChange={(e) => setPeriodDays(Number(e.target.value))}
              sx={{ height: 28, fontSize: "0.75rem" }}
            >
              {ROLLING_DAYS.map((d) => (
                <MenuItem key={d} value={d} sx={{ fontSize: "0.75rem" }}>
                  {ROLLING_DAYS_LABELS[d]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>
      </Box>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load hypes & drains data: {error}
        </Alert>
      )}
      {!loading && !error && option && (
        <Box>
          <Typography
            variant="caption"
            sx={{ fontSize: "0.72rem", fontWeight: 600, display: "block", mb: -0.5, px: 0.5 }}
          >
            Benchmark (rebased = 100) + Seasonal top-{maxRank} HYPE / bottom-{maxRank} DRAIN industries
            (● active · ○ fading · ✕ hidden)
          </Typography>
          <div ref={chartWrapperRef} style={{ position: "relative" }}>
            <EChart option={option} height={400} onReady={handleChartReady} />
          </div>
          <Typography
            variant="caption"
            sx={{ fontSize: "0.68rem", color: "text.secondary", display: "block", mt: -0.5, px: 0.5, fontStyle: "italic" }}
          >
            Click any date on the chart to inspect that month's rankings in the table below
            {selectedDate ? ` · selected: ${selectedDate}` : ""}
          </Typography>
        </Box>
      )}
      {!loading && !error && data && data.benchmark_series.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No hypes & drains data available. Run the industry sentiments pipeline to populate.
          </Typography>
        </Box>
      )}

      {/* --- Selected season's ranked industries summary table --- */}
      {!loading && !error && data && (selectedHypes.length > 0 || selectedDrains.length > 0) && (
        <Box sx={{ mt: 1.5, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1.5 }}>
          {/* HYPE */}
          <Box>
            <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 700, color: "#2e7d32" }}>
              ▲ HYPE — {selectedSeason} (lifted benchmark)
            </Typography>
            {selectedHypes.map((h) => (
              <Box
                key={`hype-${h.industry_id}`}
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  fontSize: "0.72rem",
                  py: 0.25,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <span>#{h.rank} {h.industry_label}</span>
                <span style={{ color: "#2e7d32", fontWeight: 600 }}>
                  {formatPeak(h.peak_metric_value, weighting)}
                </span>
              </Box>
            ))}
          </Box>
          {/* DRAIN */}
          <Box>
            <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 700, color: "#c62828" }}>
              ▼ DRAIN — {selectedSeason} (dragged benchmark)
            </Typography>
            {selectedDrains.map((d) => (
              <Box
                key={`drain-${d.industry_id}`}
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  fontSize: "0.72rem",
                  py: 0.25,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <span>#{d.rank} {d.industry_label}</span>
                <span style={{ color: "#c62828", fontWeight: 600 }}>
                  {formatPeak(d.peak_metric_value, weighting)}
                </span>
              </Box>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
}
