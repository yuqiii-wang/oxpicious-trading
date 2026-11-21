/**
 * RegimePanel — one code's vol regime gauges: the hv_20 / ewma_vol
 * trailing-252-row percentile ranks (0..1, left axis, 10%/90% guide
 * lines — where today's vol sits in its own past-year distribution) and
 * the hv_20 z-score (right axis). > 0.9 percentile = stretched-high vol
 * regime, < 0.1 = compressed.
 *
 * Rendered BARE inside the code group's merged vol box (the group owns
 * the parent card chrome): the section caption rides the BaseChart
 * children slot and the AI Ask "?" moves with it (useAiAskAddon). NO
 * dataZoom slider of its own (chartOption/regimeOption.ts is
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
import { buildRegimeOption } from "./chartOption/regimeOption";
import type { VolPanelProps } from "./types";

interface Props extends BaseChartProps, VolPanelProps {
  /** The shared fetch result the code group passes down. */
  rows: VolatilityRow[];
  loading?: boolean;
  error?: string | null;
}

export default function RegimePanel({
  code, name, rows, loading, error, ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () => (rows.length > 0 ? buildRegimeOption(rows, themeMode) : null),
    [rows, themeMode],
  );
  const label = name || code;
  // The AI Ask "?" rides the section caption (the bare variant has no
  // card header to host it).
  const aiAskRef = useRef<ECharts | null>(null);
  const aiAskAddon = useAiAskAddon({
    title: `${label} — Volatility Regime`,
    subtitle:
      "trailing-252d percentile ranks (10%/90% guides) + hv_20 z-score vs the past year · close on right axis",
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
        Volatility Regime
      </Typography>
      {rows.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          trailing-252d percentile ranks (10%/90% guides) + hv_20 z-score
          vs the past year · close on right axis
        </Typography>
      )}
    </BaseChart>
  );
}
