/**
 * Query-param parsing helpers shared by the ai route subfiles.
 * Full route docs: see index.ts.
 */

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
}

/** industry_ids list — comma-joined. PRESENT (even empty) scopes to exactly
 *  that set (empty → no rows); ABSENT leaves the scope to the other params. */
function industryIds(v: unknown): string[] | null {
  if (v === undefined) return null;
  return String(v).split(",").map((s) => s.trim()).filter(Boolean);
}

export { industryIds, str };
