/**
 * signal-code-trend — the shared signal-indicated per-security code
 * trend (CodeTrendChart + the FULL signal history drawn as buy/sell
 * markers), the RULES + HISTORY panels of the signals era
 * (SignalRulesPanel / SignalHistoryPanel), and the shared
 * signal-record tables that read alongside them. Single import source.
 */
export { default as SignalCodeTrendChart } from "./SignalCodeTrendChart";
export type { SignalCodeTrendChartProps } from "./SignalCodeTrendChart";
export { default as SignalHistoryPanel } from "./SignalHistoryPanel";
export type { SignalHistoryPanelProps } from "./SignalHistoryPanel";
export { default as SignalRulesPanel } from "./SignalRulesPanel";
export type { SignalRulesPanelProps } from "./SignalRulesPanel";
export { default as SignalRecordsTable } from "./SignalRecordsTable";
export type { SignalRecordsTableProps } from "./SignalRecordsTable";
export { SignalPopulationToggles } from "./SignalPopulationControls";
