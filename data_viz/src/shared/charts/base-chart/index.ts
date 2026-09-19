/**
 * Base chart kit — the shared foundation for every chart component.
 * See BaseChart.tsx / types.ts for the architecture notes.
 */
export { default as BaseChart } from "./BaseChart";
export type { BaseChartProps } from "./types";
export { baseChartOption, emptyChartOption } from "./baseOption";
export type { BaseChartOptionOverrides } from "./baseOption";
// Re-exported so chart code has a single import source for the base layer;
// the sibling fragments (commonLegend/commonGrid/commonDataZoom/axisColors)
// keep their canonical home in theme/chart-palette.ts.
export { commonTooltip } from "@/theme/chart-palette";
export { useChartThemeMode } from "./useChartThemeMode";
export { useChartData } from "./useChartData";
export type { ChartDataState } from "./useChartData";
// BaseChartProps.aiAsk references this type; re-exported so chart authors
// have a single import source for the base layer.
export type { AiAskSpec } from "@/shared/ai-ask";
