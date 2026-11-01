/**
 * keyword-maps.service — READ over the seeded keyword → category map
 * (static.keyword_maps, the Config app's Keywords tab).
 *
 * static.keyword_maps is a STATIC SEED table — rows land only via the SQL
 * seed files under database/sql/static/keyword_maps/ (wave-1 bootstrap +
 * statics/ expansion waves), never via a pipeline writer or this API, so
 * the service is read-only. The categorical columns ride the DB as
 * static.label_dim SMALLINT codes ('kw_category_type' / 'kw_market' /
 * 'kw_theme' / 'kw_pos' / 'kw_language'); they cross this boundary
 * DECODED to labels (the same convention as the external-request rows'
 * resource_type) so the UI table renders words, not codes.
 */
import { queryRows } from "./db.service.js";
import { LABEL_DECODE, type LabelDomain } from "./label-codes.js";
import type { QueryResultRow } from "pg";

export interface KeywordMapEntry {
  keyword: string;
  /** Decoded 'kw_category_type': industry | market | broadmarket | skip |
   *  adj | adv | adv_noun | adv_verb. */
  category_type: string;
  /** industry_id for industry rows; the decoded 'kw_market' / 'kw_theme'
   *  label for market / broadmarket rows; null for skip and component
   *  rows (and when undecodable). */
  category: string | null;
  category_code: number | null;
  /** Decoded 'kw_pos': noun | verb | noun_verb | function. */
  parts_of_speech: string;
  is_negation: boolean;
  /** Decoded 'kw_language': zh | en. */
  language: string;
}

function decodeCode(domain: LabelDomain, code: number): string {
  return LABEL_DECODE[domain][code] ?? String(code);
}

export async function listKeywordMaps(): Promise<KeywordMapEntry[]> {
  const rows = await queryRows<QueryResultRow>(
    `SELECT keyword, category_type, industry_id, category_code,
            parts_of_speech, is_negation, language
     FROM static.keyword_maps
     ORDER BY category_type,
              category_code NULLS LAST,
              industry_id NULLS LAST,
              keyword`,
  );
  return rows.map((r) => {
    const categoryType = decodeCode(
      "kw_category_type",
      Number(r.category_type),
    );
    const categoryCode = r.category_code === null ? null : Number(r.category_code);
    const category =
      r.industry_id !== null && r.industry_id !== undefined
        ? String(r.industry_id)
        : categoryCode !== null
          ? (decodeCode(
              categoryType === "market" ? "kw_market" : "kw_theme",
              categoryCode,
            ))
          : null;
    return {
      keyword: String(r.keyword),
      category_type: categoryType,
      category,
      category_code: categoryCode,
      parts_of_speech: decodeCode("kw_pos", Number(r.parts_of_speech)),
      is_negation: Boolean(r.is_negation),
      language: decodeCode("kw_language", Number(r.language)),
    };
  });
}
