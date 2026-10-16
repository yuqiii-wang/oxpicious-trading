/**
 * HypedVsDrainedChart — the per-security companion chart of the Margin
 * Trends page: the volatility-amplification split as a clean single-axis
 * HORIZONTAL grouped bar chart — regime groups (calm/hot/panic/quiet,
 * each labeled with its within-regime amplification ×ratio) × Drained vs
 * Hyped buyShare-tercile bars.
 *
 * Pure presentation: takes the derived analytics of ONE code from
 * chart/derive and renders via chart/hypedDrainedOption. Empty when the
 * code has no tercile history (e.g. an industry with too few share days).
 */
import { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { buildHypedDrainedOption } from "../chart/hypedDrainedOption";
import { amplificationRatio } from "../chart/derive";
import type { CodeDerived } from "../chart/types";

interface Props extends BaseChartProps {
  code: string;
  label: string;
  derived: CodeDerived | undefined;
}

export function HypedVsDrainedChart({ code, label, derived, ...base }: Props) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () => (derived ? buildHypedDrainedOption({ code, label, derived, themeMode }) : null),
    [code, label, derived, themeMode],
  );
  const ratio = amplificationRatio(derived?.bins ?? null);
  return (
    <BaseChart
      variant="bare"
      option={option}
      height={250}
      emptyText="No buyShare tercile history for this security (needs ≥60 margin-share days)."
      {...base}
    >
      {option != null && (
        <Box>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            {label} — Hyped vs Drained by market regime
            <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
              {ratio != null ? `amplification ×${ratio.toFixed(2)} (Hyped/Drained, all days)` : "amplification n/a"}
            </Typography>
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {`Bars = mean |daily ret| % of Drained (low RZ-buy-share tercile,
              faint) vs Hyped (high, saturated) days WITHIN each market
              regime; ↑ = share of up days; ×ratio on each group label =
              within-regime Hyped/Drained amplification.`}
          </Typography>
        </Box>
      )}
    </BaseChart>
  );
}
