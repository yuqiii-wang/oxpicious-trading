/**
 * signal-code-trend — the shared signal-indicated per-security code
 * trend (CodeTrendChart + the FULL signal history drawn as buy/sell
 * markers), the threshold/curve signal overlay panel, and the shared
 * signal-record tables that read alongside it. Single import source.
 */
export { default as SignalCodeTrendChart } from "./SignalCodeTrendChart";
export type { SignalCodeTrendChartProps } from "./SignalCodeTrendChart";
export { default as SignalOverlayPanel } from "./SignalOverlayPanel";
export type { SignalOverlayPanelProps } from "./SignalOverlayPanel";
export { default as SignalRecordsTable } from "./SignalRecordsTable";
export type { SignalRecordsTableProps } from "./SignalRecordsTable";
export {
  SignalPopulationToggles,
  useSignalPopulation,
} from "./SignalPopulationControls";
export type {
  SignalPopulation,
  SignalEpPass,
} from "./SignalPopulationControls";
