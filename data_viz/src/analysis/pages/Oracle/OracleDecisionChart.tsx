/**
 * Oracle action chart — the code's close curve (blue) with the
 * hindsight oracle's action markers (the shared signal-event layer) ON
 * the local extremes (5-bar-window pivots, one action PER SIDE per
 * 5-day window). Pure
 * BaseChart wrapper: the data is fetched by the owning code group (ONE
 * fetch shared with the summary + pivots table) and passed in.
 */
import { useMemo } from "react";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import type { OracleSeriesResponse } from "@shared/types";
import { CHART_HEIGHT } from "./constants";
import { buildOracleChartOption } from "./chartOption/oracleChartOption";

interface Props extends BaseChartProps {
  data: OracleSeriesResponse | null;
}

export default function OracleDecisionChart({ data, ...base }: Props) {
  const themeMode = useChartThemeMode();

  const option = useMemo(
    () =>
      data && (data.closes.length > 0 || data.rows.length > 0)
        ? buildOracleChartOption(data.closes, data.rows, themeMode)
        : null,
    [data, themeMode],
  );

  const buys = data
    ? data.rows.filter((r) => r.action === "buy").length
    : 0;
  const sells = data
    ? data.rows.filter((r) => r.action === "sell").length
    : 0;

  return (
    <BaseChart
      title="Hindsight oracle — local min/max actions"
      subtitle={
        data
          ? `${buys} buys (local minima) · ${sells} sells (local maxima) · ` +
            `5-bar pivots · 1 action per side / 5d · latest ` +
            `${data.rows.at(-1)?.date ?? "—"}`
          : undefined
      }
      option={option}
      emptyText="No oracle pivots for this code"
      height={CHART_HEIGHT}
      {...base}
    />
  );
}
