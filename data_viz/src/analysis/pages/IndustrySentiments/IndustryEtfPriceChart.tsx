/**
 * IndustryEtfPriceChart — the 1st plot in "ETF Contribution" mode.
 *
 * Fetches the daily close series for ALL ETFs tracking member indices of the
 * selected industries and renders a multi-line chart. Each ETF is rebased to
 * 100 at its own first available date using CASCADING REBASING: later-listed
 * ETFs start at the mean of already-active ETFs on their first date so they
 * blend in rather than jumping to 100.
 *
 * The chart is CLICKABLE — clicking anywhere inside the plot grid selects the
 * nearest date, which flows up to the parent and updates the as-of date for
 * the per-industry ETF contribution bar charts below.
 *
 * A vertical dashed markLine marks the currently selected date.
 *
 * A slider at the bottom controls the visible date range.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Slider,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { fetchIndustryEtfPriceSeries } from "@/lib/api-client";
import type { IndustryEtfPriceSeriesResponse } from "../../../../shared/types";
import type { IndustryEtfPriceChartProps } from "./types";
import { buildIndustryEtfPriceOption } from "./industryEtfPriceOption";

export function IndustryEtfPriceChart({
  industryIds,
  themeMode,
  selectedDate,
  onDateSelect,
}: IndustryEtfPriceChartProps) {
  const [data, setData] = useState<IndustryEtfPriceSeriesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Time slider: [startIdx, endIdx] controls the visible range of the chart.
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Merged in-plot toggle: which layer is prominent. The other layer is
  // rendered lowkey (low opacity) rather than hidden, so both are always
  // visible and only the emphasis flips.
  //   "price" → price-trend curves prominent, trading-amt bars + MA lowkey.
  //   "amt"   → trading-amt bars + MA prominent, price-trend curves lowkey.
  const [viewMode, setViewMode] = useState<"price" | "amt">("price");

  // Which MA of the per-date total ETF trading amount to draw as a line on
  // the trading-amt (right) axis. "ma5" (default) or "ma20".
  const [maMode, setMaMode] = useState<"ma5" | "ma20">("ma5");

  // Fetch ETF price series when industryIds changes.
  const industryIdsKey = industryIds.slice().sort().join(",");
  useEffect(() => {
    if (industryIds.length === 0) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustryEtfPriceSeries(industryIds)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        // Reset slider to full range when new data arrives.
        // Compute the total number of unique dates across all ETFs.
        const dateSet = new Set<string>();
        for (const etf of resp.etfs) {
          for (const r of etf.rows) dateSet.add(r.date);
        }
        const totalN = dateSet.size;
        setRange([0, Math.max(0, totalN - 1)]);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industryIdsKey]);

  const option = useMemo(
    () => (data ? buildIndustryEtfPriceOption(data, themeMode, selectedDate, range, viewMode, maMode) : null),
    [data, themeMode, selectedDate, range, viewMode, maMode],
  );

  // Stable callback for onCanvasClick — converts the x-axis category index
  // (relative to the VISIBLE slice) to a full-data date string.
  const handleCanvasClick = useMemo(() => {
    if (!data) return undefined;
    // Rebuild the global dates array to match the option builder.
    const dateSet = new Set<string>();
    for (const etf of data.etfs) {
      for (const r of etf.rows) dateSet.add(r.date);
    }
    const allDates = Array.from(dateSet).sort();
    if (allDates.length === 0) return undefined;
    return (dataIndex: number) => {
      const fullIdx = dataIndex + range[0];
      const dt = allDates[fullIdx];
      if (dt) onDateSelect(dt);
    };
  }, [data, onDateSelect, range]);

  const etfCount = data?.etfs.length ?? 0;

  const subtitle = data
    ? `${etfCount} ETF${etfCount === 1 ? "" : "s"} tracking member indices — click any date to set the as-of date` +
      (selectedDate ? ` · selected: ${selectedDate}` : "") +
      ` · cascading rebase (each ETF starts at the mean on its first date)` +
      ` · ${viewMode === "price" ? "price prominent" : "trading amt + " + (maMode === "ma20" ? "MA20" : "MA5") + " prominent"} (other lowkey)`
    : "Select industries to see their ETF price chart";

  // All dates for the slider labels.
  const allDates = useMemo(() => {
    if (!data) return [];
    const dateSet = new Set<string>();
    for (const etf of data.etfs) {
      for (const r of etf.rows) dateSet.add(r.date);
    }
    return Array.from(dateSet).sort();
  }, [data]);
  const maxIdx = allDates.length - 1;

  return (
    <ChartCard
      title="ETF Price (Cascading Rebase)"
      subtitle={subtitle}
    >
      {industryIds.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            Select one or more industries to see their member ETFs' price chart.
          </Typography>
        </Box>
      )}
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>Failed to load ETF price data: {error}</Alert>
      )}
      {!loading && !error && data && data.etfs.length > 0 && option && (
        <Stack spacing={1}>
          <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 1 }}>
            {/* Trading Amt MA selector — picks which moving average of the
                per-date total ETF trading amount is drawn as a line on the
                trading-amt (right) axis. Lives in-plot (moved here from the
                page control bar). Only shown when the "Trading Amt" toggle is
                active — the MA line is still rendered (lowkey) in Price Trend
                mode, but the dropdown is hidden to reduce clutter. */}
            {viewMode === "amt" && (
              <FormControl size="small" sx={{ minWidth: 150 }}>
                <InputLabel id="etf-price-ma-label">Trading Amt</InputLabel>
                <Select
                  labelId="etf-price-ma-label"
                  value={maMode}
                  label="Trading Amt"
                  onChange={(e) =>
                    setMaMode(e.target.value as "ma5" | "ma20")
                  }
                >
                  <MenuItem value="ma5">Trading Amt · MA5</MenuItem>
                  <MenuItem value="ma20">Trading Amt · MA20</MenuItem>
                </Select>
              </FormControl>
            )}
            {/* Merged toggle — switches which layer is prominent. The other
                layer is rendered lowkey (not hidden). Replaces the old
                "Trading Amt" on/off toggle and the page-level "ETF Price
                Trend" show/hide toggle. */}
            <ToggleButtonGroup
              value={viewMode}
              exclusive
              size="small"
              onChange={(_, v: "price" | "amt" | null) => {
                if (v) setViewMode(v);
              }}
            >
              <ToggleButton value="price">Price Trend</ToggleButton>
              <ToggleButton value="amt">Trading Amt</ToggleButton>
            </ToggleButtonGroup>
          </Box>
          <EChart
            option={option}
            height={400}
            onCanvasClick={handleCanvasClick}
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
                valueLabelFormat={(idx) => allDates[idx] ?? ""}
                sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
              />
              <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {allDates[range[0]] ?? "—"}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {allDates[range[1]] ?? "—"}
                </Typography>
              </Stack>
            </Box>
          )}
        </Stack>
      )}
      {!loading && !error && data && data.etfs.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No ETFs found tracking member indices of the selected industri{industryIds.length === 1 ? "y" : "es"}.
          </Typography>
        </Box>
      )}
    </ChartCard>
  );
}
