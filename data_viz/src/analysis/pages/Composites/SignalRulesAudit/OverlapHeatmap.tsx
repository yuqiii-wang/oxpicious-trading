/**
 * OverlapHeatmap — the Signal Rules Audit page's matrix: rows = base
 * family A, columns = other family B, cell color + label = the active
 * metric (overlap % / lift / Jaccard). The diagonal is the trivial
 * self-overlap (absent from the data — rendered as an empty cell).
 */
import { useMemo } from "react";
import {
  BaseChart,
  useChartThemeMode,
  type BaseChartProps,
} from "@/shared/charts/base-chart";
import type { SignalOverlapAuditRow } from "@shared/types";
import { buildOverlapHeatmapOption, type HeatmapMetric } from "./overlapHeatmapOption";

interface Props extends BaseChartProps {
  /** Family display labels, ordered by event count desc (both axes). */
  familyLabels: string[];
  /** Family label codes aligned with familyLabels. */
  familyCodes: number[];
  cells: SignalOverlapAuditRow[];
  metric: HeatmapMetric;
}

export default function OverlapHeatmap({
  familyLabels,
  familyCodes,
  cells,
  metric,
  ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () =>
      familyLabels.length > 1 && cells.length > 0
        ? buildOverlapHeatmapOption(
            { familyCodes, familyLabels, cells },
            metric,
            themeMode,
          )
        : null,
    [familyCodes, familyLabels, cells, metric, themeMode],
  );

  return (
    <BaseChart
      title="Family overlap matrix"
      subtitle={
        metric === "lift"
          ? "cell color = lift (saturates at 5×) — tooltip carries the raw value"
          : "cell = of the row family's signal days, the share the column family also fired on"
      }
      option={option}
      emptyText="No overlap rows for this period yet — run the audit"
      height={480}
      {...base}
    />
  );
}
