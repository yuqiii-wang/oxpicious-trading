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
 *   • Rolling  — 100-rebased benchmark vs the selected rolling_Xdays_price
 *                column (cumulative performance over the trailing X-day
 *                window).
 *
 * A rolling-days DROPDOWN lets the user pick which trailing window
 * (5 / 20 / 60 / 255 / 500 trading days) drives the shade overlay. Each
 * option selects one of the 5 pre-materialized
 * benchmark_non_this_industry_rolling_{N}days_price columns from
 * analysis.industry_attributions. Defaults to 255 days (~1 year).
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
  FormControl,
  MenuItem,
  Select,
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
} from "@shared/types";
import type { BenchmarkPriceChartProps, RollingDays } from "./types";
import {
  ROLLING_DAYS,
  DEFAULT_ROLLING_DAYS,
  ROLLING_DAYS_LABELS,
  ROLLING_DAYS_FIELD,
} from "./constants";
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

  // Trading-amount bar overlay toggle. When ON, renders a bar per date on
  // the right y-axis (yAxis 1). The bar's TOTAL = benchmark trading amount;
  // each selected industry's `benchmark_shared_weight` proportion is
  // highlighted at the bottom of the bar in the industry's color.
  const [showTradingAmt, setShowTradingAmt] = useState<boolean>(false);

  // Rolling-days dropdown: which trailing window (5/20/60/255/500) drives the
  // shade overlay. Selects one of the 5 pre-materialized
  // non_this_industry_rolling_{N}days_price columns from the API response.
  // Defaults to 255 days (~1 year).
  const [rollingDays, setRollingDays] = useState<RollingDays>(DEFAULT_ROLLING_DAYS);

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
  // Passes the SELECTED rolling_Xdays_price column (100-based cumulative
  // non-industry return factor over the trailing X-day window) — the option
  // builder scales it to the benchmark's price level as
  // `benchmark_close × rolling / 100` so the industry curve is
  // "src benchmark price + non-industry changes over the last X days".
  // This makes the gap consistent across Absolute and Percentage modes (both
  // use the SAME formula; Percentage just rebases both to 100 at first_close).
  //
  // Also passes the industry's `benchmark_shared_weight` per date — drives
  // the trading-amount bar overlay (highlighted portion = trading_amt ×
  // shared_weight / 100, anchored on the benchmark's full trading amount).
  const industryShades = useMemo<IndustryShadeData[]>(() => {
    if (!data || selectedIndustries.length === 0) return [];
    const benchmarkDates = data.rows.map((r) => r.date);
    // Resolve the row field name for the selected rolling window ONCE so the
    // inner loop doesn't re-evaluate the lookup per row.
    const fieldKey = ROLLING_DAYS_FIELD[rollingDays];

    const shades: IndustryShadeData[] = [];
    for (const sel of selectedIndustries) {
      const series = industrySeries[sel.id];
      if (!series || series.rows.length === 0) continue;

      // Build a date → rolling_Xdays_price lookup. Uses the column selected
      // by the dropdown — the option builder scales it to benchmark price
      // level in BOTH modes.
      const valueByDate = new Map<string, number | null>();
      // And a date → benchmark_shared_weight lookup for the trading-amount
      // bar overlay.
      const sharedWeightByDate = new Map<string, number | null>();
      for (const r of series.rows) {
        valueByDate.set(r.date, r[fieldKey as keyof typeof r] as number | null);
        sharedWeightByDate.set(r.date, r.benchmark_shared_weight);
      }

      // Align industry values to benchmark dates (full axis — the chart's
      // in-chart dataZoom handles viewport control).
      const values: Array<number | null> = benchmarkDates.map((dt) => {
        const v = valueByDate.get(dt);
        return v ?? null;
      });
      const shared_weights: Array<number | null> = benchmarkDates.map((dt) => {
        const v = sharedWeightByDate.get(dt);
        return v ?? null;
      });

      shades.push({
        industry_id: sel.id,
        industry_label: sel.label,
        values,
        shared_weights,
      });
    }
    return shades;
  }, [data, selectedIndustries, industrySeries, rollingDays]);

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
            undefined,
            showTradingAmt,
          )
        : null,
    [data, themeMode, selectedDate, industryShades, priceMode, showToggle, showTradingAmt],
  );

  // Stable callback for onCanvasClick — converts the x-axis category index
  // (full data, since the chart now uses an in-chart dataZoom) to a date string.
  const handleCanvasClick = useMemo(() => {
    if (!data || data.rows.length === 0) return undefined;
    return (dataIndex: number) => {
      const row = data.rows[dataIndex];
      if (row) onDateSelect(row.date);
    };
  }, [data, onDateSelect]);

  const subtitle = data
    ? `${data.name} (${data.code}) — click any date to set the as-of date${selectedDate ? ` · selected: ${selectedDate}` : ""}` +
      (hasIndustries
        ? shadesAvailable
          ? ` · ${industryShades.length} industr${industryShades.length === 1 ? "y" : "ies"} shaded · window: ${ROLLING_DAYS_LABELS[rollingDays]}`
          : " · shades require broad-market (★) benchmark"
        : "") +
      (showTradingAmt
        ? ` · trading amt bars${hasIndustries && shadesAvailable ? " (industry shared portion highlighted)" : ""}`
        : "")
    : "Select a benchmark to see its price chart";

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
          <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
            {showToggle && (
              <FormControl size="small" sx={{ minWidth: 150 }}>
                <Select
                  size="small"
                  value={rollingDays}
                  onChange={(e) => setRollingDays(Number(e.target.value) as RollingDays)}
                  sx={{ "& .MuiSelect-select": { py: 0.25, fontSize: "0.8rem" } }}
                  inputProps={{ "aria-label": "Rolling days window" }}
                >
                  {ROLLING_DAYS.map((d) => (
                    <MenuItem key={d} value={d}>{ROLLING_DAYS_LABELS[d]}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
            {showToggle && (
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
            )}
            <ToggleButtonGroup
              value={showTradingAmt ? ["on"] : []}
              size="small"
              onChange={(_, v: string[]) => {
                setShowTradingAmt(v.includes("on"));
              }}
            >
              <ToggleButton value="on">Trading Amt</ToggleButton>
            </ToggleButtonGroup>
          </Box>
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
