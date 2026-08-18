/**
 * IndustryBenchmarkAttributionChart — fetches the industry-aggregated
 * attribution rows for ONE industry from analysis.industry_attributions
 * and renders the bar chart. This is the 2nd plot (and onward — one per
 * selected industry) in "Benchmark Attribution" mode.
 *
 * The as-of `date` is passed in from the parent (set by clicking a date on
 * the benchmark price chart — the 1st plot). The `selectedBenchmarkCode`
 * (from the benchmark dropdown) is forwarded to the option builder so the
 * navigation benchmark is highlighted in the bar chart.
 *
 * Uses the shared attributionBarCommon module so the visual style stays
 * consistent with PerfAttr's fluctuationOption (grouped bars, dual Y-axes,
 * contribution + shared weight, broad-market dimming).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { fetchIndustryBenchmarkAttribution } from "@/lib/api-client";
import type { IndustryBenchmarkAttributionResponse } from "@shared/types";
import type { AttributionChartProps } from "./types";
import { buildIndustryBenchmarkAttributionOption } from "./industryBenchmarkAttributionOption";

export function IndustryBenchmarkAttributionChart({
  industryId,
  industryLabel,
  date,
  themeMode,
  selectedBenchmarkCode,
}: AttributionChartProps) {
  const [data, setData] = useState<IndustryBenchmarkAttributionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Stable key for the fetch effect — refetch when industry or date changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustryBenchmarkAttribution(industryId, date || null)
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
  }, [industryId, date]);

  const option = useMemo(
    () => (data ? buildIndustryBenchmarkAttributionOption(data, themeMode, selectedBenchmarkCode) : null),
    [data, themeMode, selectedBenchmarkCode],
  );

  return (
    <ChartCard
      title={`${industryLabel || industryId} — Benchmark Attribution`}
      subtitle={
        data
          ? `${industryLabel || industryId} — ${data.benchmarks.length} benchmarks · as-of ${data.latest_date || "—"}`
          : `${industryLabel || industryId}`
      }
    >
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>Failed to load attribution: {error}</Alert>
      )}
      {!loading && !error && data && data.benchmarks.length > 0 && option && (
        <EChart option={option} height={360} />
      )}
      {!loading && !error && data && data.benchmarks.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No benchmark attribution rows for {industryId}. Run{" "}
            <code>python -m analyze.industry_sentiments.attributions</code> to populate.
          </Typography>
        </Box>
      )}
    </ChartCard>
  );
}
