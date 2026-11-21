/**
 * VolLevelsPanel — one code's close-to-close historical vol across the
 * 5/20/60/252-row windows + the RiskMetrics EWMA vol (percent
 * annualized; the 10/120 windows stay in the tooltip payload), over the
 * security's full history (the dataZoom slices it).
 *
 * Rendered BARE inside the code group's merged vol box (the group owns
 * the parent card chrome): the section caption rides the BaseChart
 * children slot and the AI Ask "?" moves with it (useAiAskAddon). This
 * is the group's FIRST chart — the only one that shows the dataZoom
 * time slider (chartOption/volLevelsOption.ts keeps the commonDataZoom
 * slider; the other three panels react to it through the connect group).
 */
import { useMemo, useRef } from "react";
import type { ECharts } from "echarts";
import { Typography } from "@mui/material";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { VolatilityRow } from "@shared/types";
import { buildVolLevelsOption } from "./chartOption/volLevelsOption";
import type { VolPanelProps } from "./types";

interface Props extends BaseChartProps, VolPanelProps {
  /** The shared fetch result the code group passes down. */
  rows: VolatilityRow[];
  loading?: boolean;
  error?: string | null;
}

export default function VolLevelsPanel({
  code, name, rows, loading, error, ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () => (rows.length > 0 ? buildVolLevelsOption(rows, themeMode) : null),
    [rows, themeMode],
  );
  const label = name || code;
  // The AI Ask "?" rides the section caption (the bare variant has no
  // card header to host it).
  const aiAskRef = useRef<ECharts | null>(null);
  const aiAskAddon = useAiAskAddon({
    title: `${label} — Historical Volatility`,
    subtitle:
      rows.length > 0
        ? `${rows.length} daily bars · close-to-close HV windows + EWMA (λ=0.94) · percent annualized · close on right axis`
        : undefined,
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
      height={340}
      {...base}
      onReady={(inst) => {
        aiAskRef.current = inst;
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {aiAskAddon}
        Historical Volatility
      </Typography>
      {rows.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          close-to-close HV windows + EWMA (λ=0.94) · percent annualized ·
          close on right axis
        </Typography>
      )}
    </BaseChart>
  );
}
