/**
 * Analysis Advice client — the ANALYSIS-WIDE shared advice component's
 * data (GET /api/analysis-advice → analysis.analysis_advice): per
 * (analysis_item, sec_type[, industry, code]) the significant /
 * insignificant text bundle, the implications and the DYNAMIC
 * justification tables (data-derived snapshots), most-specific match
 * first (code-specific over scope-generic rows).
 */
import { fetchJson } from "./_cache";

/** One column of a dynamic justification table. */
export interface AdviceTableColumn {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
  mono?: boolean;
}

/** One dynamic justification table (title + columns spec + row arrays;
 *  cells are string | number | null — null renders as "—"). */
export interface AdviceJustificationTable {
  title: string;
  note?: string | null;
  columns: AdviceTableColumn[];
  rows: Array<Array<string | number | null>>;
}

/** One implication entry — {title, text} or a plain string (untitled). */
export type AdviceImplication = { title?: string; text?: string } | string;

/** The what-it-means text bundle — the known keys render with their own
 *  labels; extra string-valued keys render as additional paragraphs. */
export interface AdviceSignificance {
  significant?: string;
  insignificant?: string;
  basis?: string;
  [key: string]: string | undefined;
}

/** One advice row (scope + as-of + payload). */
export interface AnalysisAdvice {
  analysis_item: string;
  sec_type: string;
  industry: string;
  code: string;
  custom_text: string;
  as_of_date: string;
  significance: AdviceSignificance;
  implications: AdviceImplication[];
  justification: AdviceJustificationTable[];
  meta: Record<string, unknown>;
}

export interface AnalysisAdviceResponse {
  analysis_item: string;
  sec_type: string;
  code: string;
  /** The industry the server resolved for the code (min(industry_id) over
   *  stats.sec_classification — the signals tier's class rule). */
  industry: string;
  advice: AnalysisAdvice[];
}

/** Fetch the advice for one analysis item in a security context. */
export function fetchAnalysisAdvice(
  analysisItem: string,
  secType: string,
  code: string,
  opts?: { industry?: string; customText?: string },
): Promise<AnalysisAdviceResponse> {
  const params = new URLSearchParams({
    analysis_item: analysisItem,
    sec_type: secType,
    code,
  });
  if (opts?.industry) params.set("industry", opts.industry);
  if (opts?.customText) params.set("custom_text", opts.customText);
  return fetchJson<AnalysisAdviceResponse>(
    `/api/analysis-advice?${params.toString()}`,
  );
}
