/**
 * DecompositionPanel — one code's overnight-vs-intraday vol split (the
 * 20-day gap leg vs the open-to-close leg — which one carries the
 * volatility) plus the 5-minute realized vol and the bipower jump
 * ratio where the sec_type's intraday bars exist (NULL outside the
 * ~90-day retention window — the rv lines simply start there).
 *
 * Rendered BARE inside the code group's merged vol box (the group owns
 * the parent card chrome): the section caption rides the BaseChart
 * children slot and the AI Ask "?" moves with it (useAiAskAddon). NO
 * dataZoom slider of its own (chartOption/decompositionOption.ts is
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
import { buildDecompositionOption } from "./chartOption/decompositionOption";
import type { VolPanelProps } from "./types";

interface Props extends BaseChartProps, VolPanelProps {
  /** The shared fetch result the code group passes down. */
  rows: VolatilityRow[];
  loading?: boolean;
  error?: string | null;
}

export default function DecompositionPanel({
  code, name, rows, loading, error, ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () => (rows.length > 0 ? buildDecompositionOption(rows, themeMode) : null),
    [rows, themeMode],
  );
  const label = name || code;
  // The AI Ask "?" rides the section caption (the bare variant has no
  // card header to host it).
  const aiAskRef = useRef<ECharts | null>(null);
  const aiAskAddon = useAiAskAddon({
    title: `${label} — Overnight / Intraday / Realized`,
    subtitle:
      "20d overnight-gap vs open-to-close vol + 5-min realized vol & jump ratio (retention-limited) · close on right axis",
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
        Overnight / Intraday / Realized
      </Typography>
      {rows.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          20d overnight-gap vs open-to-close vol + 5-min realized vol &
          jump ratio (retention-limited) · close on right axis
        </Typography>
      )}
    </BaseChart>
  );
}
