/**
 * cellContext — the rule CELL's DAY-CONTEXT chips (trend + regime):
 * the two pooled-or-split axes every rule cell's PK carries (the same
 * sub signal measures different bars per (trend, regime)) and the
 * portfolio tier's member rows must show for the cell to be legible.
 * Shared by the per-code SignalRulesPanel's columns and the portfolio
 * CellRulesSurface's member rows — one palette, one vocabulary.
 */
import type { ReactNode } from "react";
import { Box, Chip, useTheme } from "@mui/material";

import { regimeAccentColor } from "@/shared/charts/regimeBands";

/** The trend chip's palette slot per trend label (theme tokens — theme
 *  reactive; top/bottom are the overextended extremes, rise/fall the
 *  RSI re-entry momentum states, all the POOLED stamp the sparse
 *  options families stamp, unknown the unclassified bucket). */
export const TREND_COLOR: Record<string, "error" | "success" | "info"
  | "warning" | "secondary"> = {
  top: "error",
  bottom: "success",
  rise: "info",
  fall: "warning",
  all: "secondary",
  unknown: "secondary",
};

/** The trend chip's SHADE GRADE by trend class — the chip backgrounds
 *  shade in step with the chart's trend bands (trendBands' graded
 *  depths): the overextended extremes (top/bottom — the trend-split
 *  cells' strongest populations) fill deepest, the directional states
 *  (rise/fall) one step lighter, all/unknown the faintest grey. */
export const TREND_SHADE_ALPHA: Record<string, number> = {
  top: 0.20,
  bottom: 0.20,
  rise: 0.10,
  fall: 0.10,
  all: 0.06,
  unknown: 0.06,
};

/** "#RRGGBB" | "rgb(..)" → "rgba(.., a)" — the palette slots are plain
 *  CSS color strings at runtime. */
export function withAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{6})$/i.exec(color.trim());
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  const rgb = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/i.exec(color.trim());
  if (rgb) return `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${alpha})`;
  return color;
}

/** The trend state chip (outlined, palette-tinted, shade-graded) — the
 *  cell's trend axis (top/bottom/rise/fall/all/unknown). */
export function TrendChip({ trend }: { trend: string }): ReactNode {
  const theme = useTheme();
  const main = theme.palette[TREND_COLOR[trend] ?? "secondary"].main;
  return (
    <Chip
      size="small"
      label={trend}
      sx={{
        height: 18,
        fontSize: "0.65rem",
        color: main,
        borderColor: main,
        bgcolor: withAlpha(main, TREND_SHADE_ALPHA[trend] ?? 0.06),
      }}
    />
  );
}

/** The regime state's compact word (calm renders as the mid dot — the
 *  default state earns no ink; all the POOLED stamp's word). */
export const regimeWord = (regime: string): string =>
  regime === "calm" ? "·" : regime === "all" ? "pool" : regime;

/** The regime state's accent-colored word — the cell's regime axis. */
export function RegimeWord({ regime }: { regime: string }): ReactNode {
  return (
    <Box
      component="span"
      sx={{
        color: regimeAccentColor(regime) ?? "text.disabled",
        fontWeight: 600,
        fontSize: "0.65rem",
        whiteSpace: "nowrap",
      }}
    >
      {regimeWord(regime)}
    </Box>
  );
}
