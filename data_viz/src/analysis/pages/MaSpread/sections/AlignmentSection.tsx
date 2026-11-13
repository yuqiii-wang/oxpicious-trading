/**
 * AlignmentSection — the MA-chain alignment row beneath Momentum
 * (analysis.mov_ave_spreads_detail's alignment_score / alignment_state,
 * auto-loaded with the momentum group): the five-leg alignment
 * (close>ma5>ma20>ma60>ma120>ma252) as a step-line trajectory over the
 * half-year window, bull (5) / bear (0) zones shaded, plus the latest
 * day's decoded verdict chip. NULL scores (insufficient MA history) gap
 * the line — they are never a verdict.
 */
import { useMemo } from "react";
import { Box, Chip, Typography } from "@mui/material";
import {
  BaseChart,
  useChartThemeMode,
} from "@/shared/charts/base-chart";
import type { MaAlignment, MovAveSpreadAlignmentDay } from "@shared/types";
import { SectionLabel } from "../SectionLabel";
import { PERIOD_GRID_SX, PERIOD_CHIP_SX } from "../constants";
import { buildAlignmentTrendOption } from "../chartOption/alignmentTrend";

/** The ma_alignment verdict chip's label/color per state. */
const ALIGNMENT_CHIP: Record<MaAlignment, { label: string; color: "success" | "warning" | "error" }> = {
  bullish: { label: "多头排列", color: "success" },
  mixed: { label: "纠缠", color: "warning" },
  bearish: { label: "空头排列", color: "error" },
};

interface Props {
  alignmentRows: MovAveSpreadAlignmentDay[] | undefined;
  loading: boolean;
}

export function AlignmentSection({ alignmentRows, loading }: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () =>
      alignmentRows && alignmentRows.length > 0
        ? buildAlignmentTrendOption(alignmentRows, themeMode)
        : null,
    [alignmentRows, themeMode],
  );
  const last = alignmentRows?.at(-1);
  const verdict =
    last?.alignment_state != null ? ALIGNMENT_CHIP[last.alignment_state] : null;
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        <SectionLabel
          id="alignment"
          active={!!last?.alignment_score}
          suffix={loading ? " — loading…" : ""}
        />
        {verdict && (
          <Chip
            label={`alignment: ${verdict.label} (${last!.alignment_score}/5)`}
            size="small"
            color={verdict.color}
            variant="outlined"
            sx={{ gridColumn: "span 3", ...PERIOD_CHIP_SX }}
          />
        )}
      </Box>
      {alignmentRows != null && alignmentRows.length > 0 && (
        <>
          <BaseChart
            variant="bare"
            title="MA-Chain Alignment"
            subtitle={`close > ma5 > ma20 > ma60 > ma120 > ma252 — score ${last?.alignment_score ?? "—"}/5 · ${last?.date ?? ""}`}
            loading={loading}
            option={option}
            emptyText="No alignment rows for this code"
            height={220}
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
