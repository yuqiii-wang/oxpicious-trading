/**
 * IndustryEtfContributionChart — fetches the per-ETF contribution rows for
 * ONE industry from analysis.industry_etf_contribution (via the API that also
 * reads stats.etf_liquidity_margin + stats.etf_basic_stats) and renders the
 * bar chart. This is the 2nd plot (and onward — one per selected industry)
 * in "ETF Contribution" mode.
 *
 * The as-of `date` is passed in from the parent (set by clicking a date on
 * the ETF price chart — the 1st plot). Each bar = one ETF showing its
 * trading amount (capital flow, left Y-axis, colored by return direction)
 * and its % share of the industry total (right Y-axis).
 */
import { useMemo } from "react";
import { BaseChart, useChartData, useChartThemeMode } from "@/shared/charts/base-chart";
import { fetchIndustryEtfContributionBars } from "@/lib/api-client";
import type { IndustryEtfContributionChartProps } from "./types";
import { buildIndustryEtfContributionOption } from "./industryEtfContributionOption";

export function IndustryEtfContributionChart({
  industryId,
  industryLabel,
  date,
}: IndustryEtfContributionChartProps) {
  const themeMode = useChartThemeMode();

  const { data, loading, error } = useChartData(
    () => fetchIndustryEtfContributionBars(industryId, date || null),
    [industryId, date],
  );

  const option = useMemo(
    () => (data ? buildIndustryEtfContributionOption(data, themeMode) : null),
    [data, themeMode],
  );

  const etfCount = data?.etfs.filter((e) => e.trading_amount != null).length ?? 0;

  return (
    <BaseChart
      title={`${industryLabel || industryId} — ETF Contribution`}
      subtitle={
        data
          ? `${industryLabel || industryId} — ${etfCount} ETF${etfCount === 1 ? "" : "s"} · as-of ${data.date || "—"}`
          : `${industryLabel || industryId}`
      }
      option={data && etfCount > 0 ? option : null}
      loading={loading}
      error={error ? `Failed to load ETF contribution: ${error}` : null}
      emptyText={
        data && etfCount === 0
          ? `No ETF trading data for ${industryId} on ${data.date || "this date"}. Run python -m analyze.industry_sentiments to populate.`
          : "No data"
      }
      height={360}
    />
  );
}
