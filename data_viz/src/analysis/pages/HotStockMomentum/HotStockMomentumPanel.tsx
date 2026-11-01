/**
 * HotStockMomentumPanel — one card per code: the security's daily stats
 * close + trading amount, with the hot-stock momentum curve from
 * analysis.hot_stock_momentum overlaid on its own axis once rows land.
 *
 * The momentum pipeline is a placeholder, so for now the chart shows the
 * close + trading amount from stats (momentum stays NULL); the nav/codes
 * universe above is the full classification, deliberately not
 * data-restricted.
 */
import { useMemo } from "react";
import { BaseChart, useChartThemeMode, useChartData } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { fetchHotStockMomentumChart } from "@/lib/api-client";
import type { HotStockMomentumChartResponse } from "@shared/types";
import { buildHotStockMomentumCurveOption } from "./hotStockMomentumCurveOption";
import type { PanelProps } from "./types";

interface Props extends BaseChartProps, PanelProps {}

export default function HotStockMomentumPanel({ code, name, secType, ...base }: Props) {
  const themeMode = useChartThemeMode();
  const { data, loading, error } = useChartData<HotStockMomentumChartResponse>(
    () => fetchHotStockMomentumChart(code, secType),
    [code, secType],
  );

  const option = useMemo(
    () =>
      data && data.rows.length > 0
        ? buildHotStockMomentumCurveOption(data, themeMode)
        : null,
    [data, themeMode],
  );

  const label = name || code;
  const hasMetric = (data?.rows ?? []).some((r) => r.momentum != null);

  return (
    <BaseChart
      title={`${label} — Hot Stock Momentum`}
      subtitle={
        data && data.rows.length > 0
          ? `${data.rows.length} daily bars · close + trading amount${hasMetric ? " · momentum" : " (momentum pending pipeline)"}`
          : undefined
      }
      loading={loading}
      error={error ? `Failed to load hot stock momentum chart: ${error}` : null}
      option={option}
      emptyText={`No close history for ${label} in stats — nothing to plot (the analysis.hot_stock_momentum pipeline is also still a placeholder).`}
      height={360}
      {...base}
    />
  );
}
