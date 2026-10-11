/**
 * ExtResourceTrendChart — daily external-request volume from
 * monitor.external_resource_stats: one stacked bar per service group
 * (request counts; services consolidated via serviceGroups.ts) + a
 * total-failed line overlay. Clicking a date selects it (the
 * vertical line re-renders and the page drills into that day's
 * service × status breakdown); clicking the selected date again clears
 * the selection. `dates` / `perService` / `failed` arrays are
 * index-aligned pivots computed by the page.
 */
import { useMemo } from "react";
import {
  BaseChart,
  useChartThemeMode,
  type BaseChartProps,
} from "@/shared/charts/base-chart";
import {
  buildExtResourceTrendOption,
  type TrendSeries,
} from "./extResourceTrendOption";

const NO_SERIES: TrendSeries[] = [];
const NO_COUNTS: number[] = [];
const NO_DATES: string[] = [];

interface Props extends BaseChartProps {
  dates?: string[];
  perService?: TrendSeries[];
  failed?: number[];
  selectedDate: string | null;
  onSelectDate: (date: string | null) => void;
}

export default function ExtResourceTrendChart({
  dates = NO_DATES,
  perService = NO_SERIES,
  failed = NO_COUNTS,
  selectedDate,
  onSelectDate,
  ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () =>
      dates.length > 0
        ? buildExtResourceTrendOption(
            dates,
            perService,
            failed,
            selectedDate,
            themeMode,
          )
        : null,
    [dates, perService, failed, selectedDate, themeMode],
  );
  const totalRequests = failed.length
    ? perService.reduce(
        (acc, s) => acc + s.counts.reduce((a, b) => a + b, 0),
        0,
      )
    : 0;
  return (
    <BaseChart
      title="External requests"
      subtitle={`${
        dates.length
          ? `${dates[0]} → ${dates[dates.length - 1]} (GMT days)`
          : "no data"
      } · ${totalRequests.toLocaleString()} requests · click a date to drill in`}
      option={option}
      emptyText="No external-resource stats in the window — run python -m database.external_resource_stats"
      height={300}
      onCanvasClick={(dataIdx) => {
        const d = dates[dataIdx] ?? null;
        onSelectDate(selectedDate === d ? null : d);
      }}
      {...base}
    />
  );
}
