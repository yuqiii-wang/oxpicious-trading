/**
 * Shared analysis-advice kit — the "A" icon button + dialog rendering the
 * analysis.analysis_advice payload (significance text, implications,
 * dynamic justification tables). Analysis-wide: any surface with a
 * (secType, code, analysisItem) context mounts AnalysisAdviceButton.
 */
export { default, default as AnalysisAdviceButton } from "./AnalysisAdviceButton";
export type { AnalysisAdviceButtonProps } from "./AnalysisAdviceButton";
export { default as AdviceJustificationTable } from "./AdviceJustificationTable";
