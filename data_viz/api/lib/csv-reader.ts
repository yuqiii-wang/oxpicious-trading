/**
 * CSV reader using papaparse to stream large CSVs with low memory footprint.
 * Returns typed rows after dynamic typing + row-level filtering.
 *
 * The reader caches the FULL parsed result keyed by absolute file path so that
 * subsequent filtered requests do not re-parse the file from disk.
 */
import fs from "fs";
import path from "path";
import Papa from "papaparse";
import { LruCache } from "./lru-cache.js";

export interface CsvReadOptions {
  /** Optional predicate to filter rows BEFORE type coercion (cheap prefilter). */
  prefilter?: (raw: Record<string, string>) => boolean;
  /** Optional row transform applied after parsing. */
  transform?: (raw: Record<string, unknown>) => unknown;
}

interface CachedCsv {
  rows: Record<string, unknown>[];
  mtimeMs: number;
}

const fileCache = new LruCache<CachedCsv>(20, 10 * 60 * 1000);

/**
 * Parse a CSV file fully (cached by path + mtime).
 * Returns the array of raw row objects (string values; dynamic typing left to caller).
 */
export function readCsvRaw(absPath: string): Record<string, unknown>[] {
  if (!fs.existsSync(absPath)) {
    throw new Error(`CSV not found: ${absPath}`);
  }
  const stat = fs.statSync(absPath);
  const cacheKey = absPath;
  const cached = fileCache.get(cacheKey);
  if (cached && cached.mtimeMs === stat.mtimeMs) {
    return cached.rows;
  }

  const content = fs.readFileSync(absPath, "utf-8");
  const result = Papa.parse<Record<string, unknown>>(content, {
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
    transformHeader: (h) => h.trim(),
  });

  const rows = (result.data || []).filter(
    (r): r is Record<string, unknown> => r != null && typeof r === "object",
  );

  fileCache.set(cacheKey, { rows, mtimeMs: stat.mtimeMs });
  return rows;
}

/**
 * Read CSV and apply a row-level transform to produce typed objects.
 */
export function readCsvTyped<T>(
  absPath: string,
  transform: (raw: Record<string, unknown>) => T | null,
  prefilter?: (raw: Record<string, unknown>) => boolean,
): T[] {
  const rawRows = readCsvRaw(absPath);
  const out: T[] = [];
  for (const raw of rawRows) {
    if (prefilter && !prefilter(raw)) continue;
    const typed = transform(raw);
    if (typed !== null) out.push(typed);
  }
  return out;
}

/**
 * Resolve a path under the project's temp_data/analysis_output/ directory.
 */
export function resolveAnalysisPath(...segments: string[]): string {
  const projectRoot = path.resolve(
    import.meta.dirname ?? __dirname,
    "..",
    "..",
  );
  // Walk up until we find the workspace root containing temp_data/
  let root = projectRoot;
  for (let i = 0; i < 5; i++) {
    if (fs.existsSync(path.join(root, "temp_data", "analysis_output"))) break;
    root = path.dirname(root);
  }
  return path.join(root, "temp_data", "analysis_output", ...segments);
}

/**
 * Coerce a value to number | null (treats "", "-", "nan" as null).
 */
export function toNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string") {
    const s = v.trim();
    if (!s || s === "-" || s.toLowerCase() === "nan") return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * Coerce a value to string (default "").
 */
export function toStr(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v);
}
