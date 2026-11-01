/**
 * Market Crowdedness sec_type normalization — thin re-export over the
 * shared classification-universe module (also used by
 * hot-stock-momentum); the universe row sources live there.
 */
export {
  normalizeIndexIndustrySecType as normalizeMcSecType,
  listFullIndexUniverseRows as listMcIndexUniverseRows,
  listFullIndustryUniverseRows as listMcIndustryUniverseRows,
} from "@api/services/analysis/classification-universe";
export type { IndexIndustrySecType as McSecType } from "@api/services/analysis/classification-universe";
