/**
 * UsageTrendChart — daily LLM-request volume from text.llm_requests
 * (GET /api/monitor/ai/usage): one stacked bar per service (total
 * tokens) + a total-request-count line on a second axis. Clicking a
 * date selects it (the page drills into that day's service × model
 * breakdown); clicking the selected date again clears the selection.
 * `dates` / `perService` / `requests` arrays are index-aligned pivots
 * computed by the page.
 */
import { useMemo } from "react";
import {
  BaseChart,
  useChartThemeMode,
  type BaseChartProps,
} from "@/shared/charts/base-chart";
import {
  buildAiUsageTrendOption,
  type UsageSeries,
} from "./aiUsageTrendOption";

const NO_SERIES: UsageSeries[] = [];
const NO_COUNTS: number[] = [];
const NO_DATES: string[] = [];

interface Props extends BaseChartProps {
  dates?: string[];
  perService?: UsageSeries[];
  requests?: number[];
  selectedDate: string | null;
  onSelectDate: (date: string | null) => void;
}

export default function UsageTrendChart({
  dates = NO_DATES,
  perService = NO_SERIES,
  requests = NO_COUNTS,
  selectedDate,
  onSelectDate,
  ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () =>
      dates.length > 0
        ? buildAiUsageTrendOption(
            dates,
            perService,
            requests,
            selectedDate,
            themeMode,
          )
        : null,
    [dates, perService, requests, selectedDate, themeMode],
  );
  const totalTokens = perService.reduce(
    (acc, s) => acc + s.tokens.reduce((a, b) => a + b, 0),
    0,
  );
  const totalRequests = requests.reduce((a, b) => a + b, 0);
  return (
    <BaseChart
      title="LLM request tokens"
      subtitle={`${
        dates.length
          ? `${dates[0]} → ${dates[dates.length - 1]} (GMT days)`
          : "no data"
      } · ${totalRequests.toLocaleString()} requests · ${totalTokens.toLocaleString()} tokens · click a date to drill in`}
      option={option}
      emptyText="No LLM requests in the window — the text.llm_requests log is empty for these days"
      height={300}
      onCanvasClick={(dataIdx) => {
        const d = dates[dataIdx] ?? null;
        onSelectDate(selectedDate === d ? null : d);
      }}
      {...base}
    />
  );
}
