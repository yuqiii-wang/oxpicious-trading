/**
 * portfolio-signal — the shared PORTFOLIO tier surfaces (the
 * signal-code-trend kit's portfolio sibling): rule groups of the
 * portfolios formed from a SET of codes (industry pairs for now),
 * rendered as the TWO SEPARATE surfaces of the 2026-10-21 split —
 * PortfolioSurface (portfolio_signal_rules + portfolio_signal_
 * performance: construction · benefit audit · group gates) over
 * CellRulesSurface (signal_rules + history_signal_performance: the
 * coordinated cells with their own evidence). Single import source.
 */
export { default as PortfolioSignalRulesPanel } from "./PortfolioSignalRulesPanel";
export type { PortfolioSignalRulesPanelProps } from "./PortfolioSignalRulesPanel";
export { default as PortfolioSurface } from "./portfolioSurface";
export { default as CellRulesSurface } from "./cellSurface";
export { default as PairLegsViz } from "./PairLegsViz";
export { default as PortfolioSignalHistoryPanel } from "./PortfolioSignalHistoryPanel";
export type { PortfolioSignalHistoryPanelProps } from "./PortfolioSignalHistoryPanel";
