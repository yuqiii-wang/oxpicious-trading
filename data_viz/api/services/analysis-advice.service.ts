/**
 * Analysis advice service — reads analysis.analysis_advice, the
 * analysis-wide shared advice store behind the UI "Analysis Advice"
 * component (the "A" icon): per (analysis_item scoped to sec_type /
 * industry / code) the significant-vs-insignificant text, the implications
 * and the dynamic justification tables.
 *
 * Lookup resolves the industry the way the signals tier resolves it
 * (min(industry_id) over the code's stats.sec_classification rows — the
 * curve tier's index-class rule) and falls back code-specific ->
 * scope-generic: code IN (given, '') and industry IN (resolved, ''),
 * ordered most-specific first ('' scope components never match NULL — the
 * table stores '' by contract).
 */
import { queryRows, formatDate } from "./db.service.js";

/** One advice row as stored (jsonb columns arrive parsed from pg). */
export interface AnalysisAdviceRecord {
  analysis_item: string;
  sec_type: string;
  industry: string;
  code: string;
  custom_text: string;
  as_of_date: string;
  significance: Record<string, unknown>;
  implications: unknown[];
  justification: Array<{
    title: string;
    note?: string | null;
    columns: Array<{ key: string; label: string; align?: string; mono?: boolean }>;
    rows: unknown[][];
  }>;
  meta: Record<string, unknown>;
}

/** The signals tier's index-class resolution for one (sec_type, code). */
async function resolveIndustry(
  secType: string,
  code: string,
): Promise<string> {
  if (!code) return "";
  const rows = await queryRows<{ industry_id: string | null }>(
    `SELECT min(industry_id) AS industry_id
       FROM stats.sec_classification
      WHERE type = $1 AND code = $2
        AND industry_id IS NOT NULL AND industry_id <> ''`,
    [secType, code],
  );
  return rows[0]?.industry_id ?? "";
}

export async function getAnalysisAdvice(
  analysisItem: string,
  secType: string,
  code: string,
  industry?: string,
  customText?: string,
): Promise<{ industry: string; advice: AnalysisAdviceRecord[] }> {
  const resolvedIndustry =
    industry && industry.trim() !== ""
      ? industry.trim()
      : await resolveIndustry(secType, code);
  const advice = await queryRows<AnalysisAdviceRecord>(
    `SELECT analysis_item, sec_type, industry, code, custom_text,
            as_of_date, significance, implications, justification, meta
       FROM analysis.analysis_advice
      WHERE analysis_item = $1
        AND sec_type = $2
        AND code IN ($3, '')
        AND industry IN ($4, '')
        AND custom_text = $5
      ORDER BY (code <> '') DESC, (industry <> '') DESC, as_of_date DESC`,
    [analysisItem, secType, code, resolvedIndustry, customText ?? ""],
  );
  return {
    industry: resolvedIndustry,
    advice: advice.map((r) => ({ ...r, as_of_date: formatDate(r.as_of_date) })),
  };
}
