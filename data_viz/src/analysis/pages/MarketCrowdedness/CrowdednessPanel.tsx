/**
 * CrowdednessPanel — one card per code: the security's daily stats close
 * + trading amount, with the crowdedness_score curve from
 * analysis.market_crowdedness overlaid on its own axis (the four RAW
 * pillars — concentration / flow change / co-movement / valuation —
 * with their history percentiles, the turnover-concentration evidence
 * (top 1/5/10% shares, effective-N) and the stable hot-set names land
 * in the tooltip).
 *
 * The nav/codes universe above is the full classification, deliberately
 * not data-restricted; codes outside the pipeline scope (index side =
 * the 10 study indices) plot close + amount only.
 */
import { useMemo } from "react";
import { BaseChart, useChartThemeMode, useChartData } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { fetchMarketCrowdednessChart } from "@/lib/api-client";
import type { MarketCrowdednessChartResponse } from "@shared/types";
import { buildCrowdednessCurveOption } from "./crowdednessCurveOption";
import type { PanelProps } from "./types";

interface Props extends BaseChartProps, PanelProps {}

export default function CrowdednessPanel({ code, name, secType, ...base }: Props) {
  const themeMode = useChartThemeMode();
  const { data, loading, error } = useChartData<MarketCrowdednessChartResponse>(
    () => fetchMarketCrowdednessChart(code, secType),
    [code, secType],
  );

  const option = useMemo(
    () =>
      data && data.rows.length > 0
        ? buildCrowdednessCurveOption(data, themeMode)
        : null,
    [data, themeMode],
  );

  const label = name || code;
  const hasMetric = (data?.rows ?? []).some((r) => r.crowdedness_score != null);

  return (
    <BaseChart
      title={`${label} — Market Crowdedness`}
      subtitle={
        data && data.rows.length > 0
          ? `${data.rows.length} daily bars · close + trading amount${hasMetric ? " · crowdedness" : " (no crowdedness rows for this code)"}`
          : undefined
      }
      loading={loading}
      error={error ? `Failed to load market crowdedness chart: ${error}` : null}
      option={option}
      emptyText={`No close history for ${label} in stats — nothing to plot.`}
      height={360}
      {...base}
    />
  );
}
