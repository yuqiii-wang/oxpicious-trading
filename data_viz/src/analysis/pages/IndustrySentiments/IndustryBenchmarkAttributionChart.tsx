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
import { useMemo } from "react";
import { BaseChart, useChartData, useChartThemeMode } from "@/shared/charts/base-chart";
import type { AiAskSpec } from "@/shared/ai-ask";
import { fetchIndustryBenchmarkAttribution } from "@/lib/api-client";
import type { AttributionChartProps } from "./types";
import { buildIndustryBenchmarkAttributionOption } from "./industryBenchmarkAttributionOption";

export function IndustryBenchmarkAttributionChart({
  industryId,
  industryLabel,
  date,
  selectedBenchmarkCode,
}: AttributionChartProps) {
  const themeMode = useChartThemeMode();

  const { data, loading, error } = useChartData(
    () => fetchIndustryBenchmarkAttribution(industryId, date || null),
    [industryId, date],
  );

  const option = useMemo(
    () => (data ? buildIndustryBenchmarkAttributionOption(data, themeMode, selectedBenchmarkCode) : null),
    [data, themeMode, selectedBenchmarkCode],
  );

  // Rich AI Ask semantics for this chart (instruments/scope + series units).
  const aiAsk = useMemo<AiAskSpec>(
    () => ({
      intro:
        `Benchmark attribution for the ${industryLabel || industryId} industry: ` +
        "each bar shows how much of one benchmark index's return is contributed " +
        "by this industry (Contribution, left axis) and how the industry's " +
        "weight inside the benchmark compares to its broad-market weight " +
        "(Benchmark Wt / Industry Wt, right axis). The highlighted bar is the " +
        "currently selected navigation benchmark; other bars are dimmed. " +
        "Compare bars to see which benchmarks this industry drives most — a " +
        "high contribution vs weight means the industry over-drives that " +
        "benchmark.",
      instruments: [{ code: industryId, name: industryLabel || undefined, assetClass: "industry" }],
      series: [
        { name: "Contribution", unit: "pp", description: "return contribution to the benchmark index, percentage points" },
        { name: "Benchmark Wt", unit: "%", description: "the industry's weight inside the benchmark index" },
        { name: "Industry Wt", unit: "%", description: "the industry's broad-market reference weight" },
      ],
      notes: [`As-of date: ${date || "latest"}`],
    }),
    [industryId, industryLabel, date],
  );

  return (
    <BaseChart
      title={`${industryLabel || industryId} — Benchmark Attribution`}
      subtitle={
        data
          ? `${industryLabel || industryId} — ${data.benchmarks.length} benchmarks · as-of ${data.latest_date || "—"}`
          : `${industryLabel || industryId}`
      }
      aiAsk={aiAsk}
      option={data && data.benchmarks.length > 0 ? option : null}
      loading={loading}
      error={error ? `Failed to load attribution: ${error}` : null}
      emptyText={
        data && data.benchmarks.length === 0
          ? `No benchmark attribution rows for ${industryId}. Run python -m analyze.industry_sentiments.attributions to populate.`
          : "No data"
      }
      height={360}
    />
  );
}
