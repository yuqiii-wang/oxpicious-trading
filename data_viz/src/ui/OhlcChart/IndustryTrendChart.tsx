/**
 * IndustryTrendChart — the chart BODY of CodeTrendChart's industry mode
 * (`industryId` prop): one raw close-price line per member index of the
 * industry, built by industryTrendOption.ts.
 *
 * Not a standalone chart: CodeTrendChart owns the fetch lifecycle, the
 * loading / error / empty states and the card chrome, and delegates here
 * through its renderChart hook (the class cannot call the reactive theme
 * hook itself, so the body is this small function component). Rebuilds are
 * memoized on (data, themeMode) so a theme toggle re-colors the lines
 * immediately while unrelated parent re-renders leave the canvas alone.
 */
import { useMemo } from "react";
import type { ECharts } from "echarts";
import EChart from "@/shared/charts/EChart";
import { useChartThemeMode } from "@/shared/charts/base-chart";
import type { CodeTrendIndustryData } from "./codeTrendTypes";
import { buildIndustryTrendOption } from "./industryTrendOption";

interface Props {
  data: CodeTrendIndustryData;
  height: number;
  /** Passthrough for the enclosing CodeTrendChart's chart-instance hook. */
  onChartReady?: (chart: ECharts) => void;
}

export default function IndustryTrendChart({ data, height, onChartReady }: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () => buildIndustryTrendOption(data, themeMode),
    [data, themeMode],
  );
  return <EChart option={option} height={height} onReady={onChartReady} />;
}
