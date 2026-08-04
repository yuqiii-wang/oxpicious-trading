/**
 * BenchmarkPriceChart — the 1st plot in "Benchmark Attribution" mode.
 *
 * Fetches the selected benchmark's daily close + daily return series and
 * renders a line chart. When `selectedIndustries` is non-empty, also fetches
 * each industry's non-this-industry price series and overlays green/red shades
 * between the benchmark curve and each industry's non-this-industry curve.
 *
 * A toggle (Today / Rolling) switches between:
 *   • Today    — raw close vs non_this_industry_price (daily snapshot).
 *   • Rolling  — 100-rebased benchmark vs non_this_industry_rolling_price
 *                (cumulative performance from the benchmark's first close).
 *
 * The chart is CLICKABLE — clicking anywhere inside the plot grid selects the
 * nearest date (via onCanvasClick), which flows up to the parent and updates
 * the as-of date.
 *
 * A vertical dashed markLine marks the currently selected date so the user
 * can see which date the attribution plots are showing.
 *
 * Shades are only available for broad-market (★) benchmarks. For non-broad
 * benchmarks, the industries' non_this_industry_* columns are NULL and no
 * shades are drawn (a helper message is shown).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Slider,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import {
  fetchBenchmarkPriceChart,
  fetchIndustryAttributionPriceSeries,
} from "@/lib/api-client";
import type {
  BenchmarkPriceChartResponse,
  IndustryAttributionPriceSeriesResponse,
} from "../../../../shared/types";
import type { BenchmarkPriceChartProps } from "./types";
import { buildBenchmarkPriceOption, type IndustryShadeData } from "./benchmarkPriceOption";

type PriceMode = "rolling" | "today";

export function BenchmarkPriceChart({
  benchmarkCode,
  themeMode,
  selectedDate,
  onDateSelect,
  selectedIndustries,
}: BenchmarkPriceChartProps) {
  const [data, setData] = useState<BenchmarkPriceChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Per-industry non-this-industry price series (keyed by industry_id).
  const [industrySeries, setIndustrySeries] = useState<
    Record<string, IndustryAttributionPriceSeriesResponse>
  >({});
  const [industryLoading, setIndustryLoading] = useState(false);

  // Price mode toggle: "rolling" (Percentage — 100-based, rebased to visible
  // window start) vs "today" (Absolute — raw prices).
  const [priceMode, setPriceMode] = useState<PriceMode>("rolling");

  // Time slider: [startIdx, endIdx] controls the visible range of the chart.
  // Initialized to full range when data loads; reset when benchmark changes.
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Fetch benchmark price when benchmarkCode changes.
  useEffect(() => {
    if (!benchmarkCode) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchBenchmarkPriceChart(benchmarkCode)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        // Reset slider to full range when new data arrives.
        setRange([0, Math.max(0, resp.rows.length - 1)]);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [benchmarkCode]);

  // Fetch each selected industry's non-this-industry price series in parallel.
  // Re-fetches when the benchmark code or the set of selected industry IDs
  // changes. Uses a joined-key string so the effect fires once per change.
  const industryIdsKey = selectedIndustries.map((s) => s.id).sort().join(",");
  useEffect(() => {
    if (!benchmarkCode || selectedIndustries.length === 0) {
      setIndustrySeries({});
      return;
    }
    let cancelled = false;
    setIndustryLoading(true);
    Promise.all(
      selectedIndustries.map((s) =>
        fetchIndustryAttributionPriceSeries(s.id, benchmarkCode).then(
          (resp) => [s.id, resp] as const,
        ),
      ),
    )
      .then((results) => {
        if (cancelled) return;
        const map: Record<string, IndustryAttributionPriceSeriesResponse> = {};
        for (const [id, resp] of results) map[id] = resp;
        setIndustrySeries(map);
        setIndustryLoading(false);
      })
      .catch(() => {
        // Non-fatal — shades just won't appear.
        if (cancelled) return;
        setIndustrySeries({});
        setIndustryLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [benchmarkCode, industryIdsKey]);

  // Build the aligned industry shade data for the option builder.
  // ALWAYS passes non_this_industry_rolling_price (the 100-based cumulative
  // non-industry return factor) — the option builder scales it to the
  // benchmark's price level as `benchmark_close × rolling / 100` so the
  // industry curve is "src benchmark price + cumulative non-industry changes".
  // This makes the gap consistent across Absolute and Percentage modes (both
  // use the SAME formula; Percentage just rebases both to 100 at first_close).
  const industryShades = useMemo<IndustryShadeData[]>(() => {
    if (!data || selectedIndustries.length === 0) return [];
    const benchmarkDates = data.rows.map((r) => r.date);

    const shades: IndustryShadeData[] = [];
    for (const sel of selectedIndustries) {
      const series = industrySeries[sel.id];
      if (!series || series.rows.length === 0) continue;

      // Build a date → rolling_price lookup. Always use rolling_price — the
      // option builder scales it to benchmark price level in BOTH modes.
      const valueByDate = new Map<string, number | null>();
      for (const r of series.rows) {
        valueByDate.set(r.date, r.non_this_industry_rolling_price);
      }

      // Align industry values to benchmark dates (full axis — option builder
      // will slice to the visible range).
      const values: Array<number | null> = benchmarkDates.map((dt) => {
        const v = valueByDate.get(dt);
        return v ?? null;
      });

      shades.push({
        industry_id: sel.id,
        industry_label: sel.label,
        values,
      });
    }
    return shades;
  }, [data, selectedIndustries, industrySeries]);

  // Check if the benchmark is broad-market (shades only available for broad).
  const isBroadMarket = useMemo(() => {
    const first = Object.values(industrySeries)[0];
    return first?.is_broad_market ?? null;
  }, [industrySeries]);

  const hasIndustries = selectedIndustries.length > 0;
  const shadesAvailable = isBroadMarket === true;
  const showToggle = hasIndustries && shadesAvailable;

  const option = useMemo(
    () =>
      data
        ? buildBenchmarkPriceOption(
            data,
            themeMode,
            selectedDate,
            showToggle ? industryShades : [],
            showToggle ? priceMode : "today",
            range,
          )
        : null,
    [data, themeMode, selectedDate, industryShades, priceMode, showToggle, range],
  );

  // Stable callback for onCanvasClick — converts the x-axis category index
  // (relative to the VISIBLE slice) to a full-data date string.
  const handleCanvasClick = useMemo(() => {
    if (!data || data.rows.length === 0) return undefined;
    return (dataIndex: number) => {
      // dataIndex is relative to the visible slice. Add range[0] to get the
      // full-data index, then look up the date.
      const fullIdx = dataIndex + range[0];
      const row = data.rows[fullIdx];
      if (row) onDateSelect(row.date);
    };
  }, [data, onDateSelect, range]);

  const subtitle = data
    ? `${data.name} (${data.code}) — click any date to set the as-of date${selectedDate ? ` · selected: ${selectedDate}` : ""}` +
      (hasIndustries
        ? shadesAvailable
          ? ` · ${industryShades.length} industr${industryShades.length === 1 ? "y" : "ies"} shaded`
          : " · shades require broad-market (★) benchmark"
        : "")
    : "Select a benchmark to see its price chart";

  // All dates for the slider labels.
  const allDates = data?.rows.map((r) => r.date) ?? [];
  const maxIdx = allDates.length - 1;

  return (
    <ChartCard
      title="Benchmark Price"
      subtitle={subtitle}
    >
      {!benchmarkCode && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            Select a benchmark from the dropdown above.
          </Typography>
        </Box>
      )}
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>Failed to load benchmark price: {error}</Alert>
      )}
      {!loading && !error && data && data.rows.length > 0 && option && (
        <Stack spacing={1}>
          {showToggle && (
            <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
              <ToggleButtonGroup
                value={priceMode}
                exclusive
                size="small"
                onChange={(_, v: PriceMode | null) => {
                  if (v) setPriceMode(v);
                }}
              >
                <ToggleButton value="rolling">Percentage</ToggleButton>
                <ToggleButton value="today">Absolute</ToggleButton>
              </ToggleButtonGroup>
            </Box>
          )}
          {hasIndustries && !shadesAvailable && industrySeries !== undefined && Object.keys(industrySeries).length > 0 && (
            <Alert severity="info" sx={{ py: 0.5 }}>
              Non-industry shades are only available for broad-market (★) benchmarks.
              Select a starred benchmark to see the shades.
            </Alert>
          )}
          {industryLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 1 }}>
              <CircularProgress size={20} />
            </Box>
          )}
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
      {!loading && !error && data && data.rows.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No price data for benchmark {benchmarkCode}.
          </Typography>
        </Box>
      )}
    </ChartCard>
  );
}
