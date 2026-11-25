/**
 * AlignmentSection — the MA-chain alignment section beneath the main
 * plot chart (analysis.mov_ave_spreads_detail's alignment_score /
 * alignment_state, auto-loaded with the momentum group): the five-leg alignment
 * (close>ma5>ma20>ma60>ma120>ma252) as a step-line trajectory over the
 * half-year window, bull (5) / bear (0) zones shaded. NULL scores
 * (insufficient MA history) gap the line — they are never a verdict.
 * The per-date VERDICT (多头排列 / 纠缠 / 空头排列 + score) rides the
 * chart's hover tooltip (chartOption/alignmentTrend) — the former
 * latest-day verdict chip was folded into it (2026-11-14).
 *
 * No dataZoom slider of its own — `zoom` is the panel's ONE synced
 * window (types.ChartZoomRange): the main plot's slider drives it and
 * this chart's inside zoom follows. `onReady`/`onEvents` pass through so
 * the panel wires the hover crosshair sync (shared/charts/tooltipSync —
 * hover on either chart raises the vertical axisPointer + tooltip on
 * both).
 */
import { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import type { ECharts } from "echarts";
import {
  BaseChart,
  useChartThemeMode,
} from "@/shared/charts/base-chart";
import type { MovAveSpreadAlignmentDay } from "@shared/types";
import { fmtDate } from "@/shared/datetime";
import { SectionLabel } from "../SectionLabel";
import { PERIOD_GRID_SX } from "../constants";
import type { ChartZoomRange } from "../types";
import { buildAlignmentTrendOption } from "../chartOption/alignmentTrend";

interface Props {
  alignmentRows: MovAveSpreadAlignmentDay[] | undefined;
  loading: boolean;
  /** The panel's shared dataZoom window (main plot's slider drives it). */
  zoom?: ChartZoomRange | null;
  /** Instance capture for the panel's cross-chart hover sync. */
  onReady?: (instance: ECharts) => void;
  /** Hover/leave sync events forwarded from the panel. */
  onEvents?: Record<string, (params: unknown) => void>;
}

export function AlignmentSection({ alignmentRows, loading, zoom, onReady, onEvents }: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () =>
      alignmentRows && alignmentRows.length > 0
        ? buildAlignmentTrendOption(alignmentRows, themeMode, zoom)
        : null,
    [alignmentRows, themeMode, zoom],
  );
  const last = alignmentRows?.at(-1);
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        <SectionLabel
          id="alignment"
          // A verdict exists whenever the score is non-NULL — score 0 is a
          // VALID full-bearish verdict, not "no data" (!!0 would miss it).
          active={last?.alignment_score != null}
          suffix={loading ? " — loading…" : ""}
        />
      </Box>
      {alignmentRows != null && alignmentRows.length > 0 && (
        <>
          <BaseChart
            variant="bare"
            title="MA-Chain Alignment"
            subtitle={`close > ma5 > ma20 > ma60 > ma120 > ma252 — score ${last?.alignment_score ?? "—"}/5 · ${fmtDate(last?.date)}`}
            loading={loading}
            option={option}
            emptyText="No alignment rows for this code"
            height={220}
            onReady={onReady}
            onEvents={onEvents}
          />
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
          >
            score = count of positive legs among price&gt;ma5, ma5&gt;ma20,
            ma20&gt;ma60, ma60&gt;ma120, ma120&gt;ma252 (5 = full
            bullish 多头排列 · 0 = full bearish 空头排列) · NULL = MA
            history not yet sufficient
          </Typography>
        </>
      )}
    </>
  );
}
