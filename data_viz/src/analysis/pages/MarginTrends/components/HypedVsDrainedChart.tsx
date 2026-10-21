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
import { useMemo, useRef } from "react";
import { Box, Typography } from "@mui/material";
import type { ECharts } from "echarts";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { INSIGHT_PAGES, useAiAskAddon, type AiAskSpec } from "@/shared/ai-ask";
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
  // The regime split needs stats.market_regimes rows for the code —
  // built for etf/index/stock always and for industries by
  // `builds.market_regimes --sec-type industry` (the composite series).
  // Codes without rows (young industries, or the build not yet run)
  // degrade to a specific empty state instead of a bare axis grid.
  const hasRegimeData = useMemo(
    () =>
      !!derived?.regimeBins &&
      Object.values(derived.regimeBins).some(
        (pair) => pair[0].n > 0 || pair[1].n > 0,
      ),
    [derived],
  );
  const option = useMemo(
    () =>
      derived && hasRegimeData
        ? buildHypedDrainedOption({ code, label, derived, themeMode })
        : null,
    [code, label, derived, themeMode, hasRegimeData],
  );
  const ratio = amplificationRatio(derived?.bins ?? null);

  // ---- AI Ask (insight: derivatives.hyped-vs-drained) ----------------------
  const chartRef = useRef<ECharts | null>(null);
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      insight: INSIGHT_PAGES.hypedVsDrained,
      intro:
        "Hyped vs Drained by market regime: horizontal grouped bars of mean " +
        "|daily return| % on Drained (low RZ-buy-share tercile, faint) vs " +
        "Hyped (high, saturated) days within each market regime (calm/hot/" +
        "panic/quiet), each labeled with its within-regime amplification " +
        "×ratio. Hyped > Drained = margin chase amplifies moves in that " +
        "regime.",
      instruments: [{ code, name: label, assetClass: "industry" }],
      notes: [
        "Regime split needs stats.market_regimes rows for the code; codes without them degrade to the empty state.",
      ],
    }),
    [code, label],
  );
  const aiAskAddon = useAiAskAddon({
    title: `${label} — Hyped vs Drained`,
    option,
    spec: option != null ? aiAskSpec : null,
    getInstance: () => chartRef.current,
  });
  return (
    <BaseChart
      variant="bare"
      option={option}
      height={250}
      emptyText={
        hasRegimeData
          ? "No buyShare tercile history for this security (needs ≥60 margin-share days)."
          : "No market-regime labels for this security — the regime split needs stats.market_regimes rows (run builds.market_regimes; industry rows come from --sec-type industry)."
      }
      onReady={(inst) => {
        chartRef.current = inst;
      }}
      {...base}
    >
      {option != null && (
        <Box>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            {aiAskAddon}
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
