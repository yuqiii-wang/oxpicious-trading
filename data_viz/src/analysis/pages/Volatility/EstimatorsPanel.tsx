/**
 * EstimatorsPanel — one code's 20-day estimator comparison:
 * close-to-close HV vs the Parkinson / Garman-Klass / Rogers-Satchell /
 * Yang-Zhang daily-OHLC estimators (percent annualized). The spread
 * between the lines is the estimator-efficiency story — CC is the least
 * efficient, YZ the most complete (handles both gaps and drift).
 *
 * Rendered BARE inside the code group's merged vol box (the group owns
 * the parent card chrome): the section caption rides the BaseChart
 * children slot and the AI Ask "?" moves with it (useAiAskAddon). NO
 * dataZoom slider of its own (chartOption/estimatorsOption.ts is
 * inside-only) — this chart reacts to the group's top time slider
 * through the echarts.connect group.
 */
import { useMemo, useRef } from "react";
import type { ECharts } from "echarts";
import { Typography } from "@mui/material";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { VolatilityRow } from "@shared/types";
import { buildEstimatorsOption } from "./chartOption/estimatorsOption";
import type { VolPanelProps } from "./types";

interface Props extends BaseChartProps, VolPanelProps {
  /** The shared fetch result the code group passes down. */
  rows: VolatilityRow[];
  loading?: boolean;
  error?: string | null;
}

export default function EstimatorsPanel({
  code, name, rows, loading, error, ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () => (rows.length > 0 ? buildEstimatorsOption(rows, themeMode) : null),
    [rows, themeMode],
  );
  const label = name || code;
  // The AI Ask "?" rides the section caption (the bare variant has no
  // card header to host it).
  const aiAskRef = useRef<ECharts | null>(null);
  const aiAskAddon = useAiAskAddon({
    title: `${label} — 20-day Estimator Comparison`,
    subtitle:
      "close-to-close vs Parkinson / Garman-Klass / Rogers-Satchell / Yang-Zhang · % annualized · close on right axis",
    option,
    getInstance: () => aiAskRef.current,
  });
  return (
    <BaseChart
      variant="bare"
      loading={loading}
      error={error ? `Failed to load volatility series: ${error}` : null}
      option={option}
      emptyText={`No volatility rows for ${label} — run analysis.volatility first.`}
      height={320}
      {...base}
      onReady={(inst) => {
        aiAskRef.current = inst;
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {aiAskAddon}
        20-day Estimator Comparison
      </Typography>
      {rows.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          close-to-close vs Parkinson / Garman-Klass / Rogers-Satchell /
          Yang-Zhang · % annualized · close on right axis
        </Typography>
      )}
    </BaseChart>
  );
}
