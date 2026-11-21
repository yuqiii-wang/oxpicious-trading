/**
 * ServiceRunTrendChart — the PER-SERVICE daily run-count curve
 * (monitor.service_audit run starts of ONE service per Shanghai-local
 * day, GET /api/monitor/stream/runs/daily; the service comes from the
 * caller — a clicked row's expansion — never a picker). Clicking a day
 * selects it (the panel below drills into that day's run list);
 * clicking the selected day again clears the selection. `dates` /
 * `counts` / `abnormalCounts` are index-aligned arrays computed by the
 * parent ServiceRunTrend.
 */
import { useMemo } from "react";
import {
  BaseChart,
  useChartThemeMode,
  type BaseChartProps,
} from "@/shared/charts/base-chart";
import { buildServiceRunTrendOption } from "./serviceRunTrendOption";

const NO_COUNTS: number[] = [];
const NO_DATES: string[] = [];

interface Props extends BaseChartProps {
  /** The service the curve plots (echoed in the subtitle). */
  service: string;
  dates?: string[];
  counts?: number[];
  abnormalCounts?: number[];
  selectedDate: string | null;
  onSelectDate: (date: string | null) => void;
}

export default function ServiceRunTrendChart({
  service,
  dates = NO_DATES,
  counts = NO_COUNTS,
  abnormalCounts = NO_COUNTS,
  selectedDate,
  onSelectDate,
  ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () =>
      dates.length > 0
        ? buildServiceRunTrendOption(
            dates,
            counts,
            abnormalCounts,
            selectedDate,
            themeMode,
          )
        : null,
    [dates, counts, abnormalCounts, selectedDate, themeMode],
  );
  const totalRuns = counts.reduce((a, b) => a + b, 0);
  return (
    <BaseChart
      title="Daily runs"
      subtitle={`${
        dates.length > 0
          ? `${service} · ${dates[0]} → ${dates[dates.length - 1]} · ${totalRuns.toLocaleString()} runs`
          : service
      } · click a day to list its runs`}
      option={option}
      emptyText="No runs on record for this service — monitor.service_audit has no rows in this window"
      height={260}
      onCanvasClick={(dataIdx) => {
        const d = dates[dataIdx] ?? null;
        onSelectDate(selectedDate === d ? null : d);
      }}
      {...base}
    />
  );
}
