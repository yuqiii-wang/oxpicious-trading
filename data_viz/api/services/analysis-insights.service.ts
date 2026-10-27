/**
 * analysis-insights.service — Ask AI insight metrics.
 *
 * Serves the insight registry (analysis_insights.insight_templates,
 * INSERT-seeded by database/sql/analysis/static/analysis_insights/02..05
 * — the per-tab data files, the single source of truth) and resolves one
 * analysis page's metric VALUES for an
 * ask context (the classification-nav security + the as-of date chosen in
 * the code trend / modal).
 *
 * Resolution is the CONTRACT mirrored by analysis/analysis_insights/resolver.py
 * (see the 01_ DDL header there): table-kind metrics resolve HERE
 * via spec-driven SQL (fast path — the modal fills instantly); compute-kind
 * metrics (and table metrics' on-demand compute fallbacks) spawn
 * `python -m analysis.analysis_insights resolve --only … --marker` and
 * merge its result. Keep the two implementations in sync.
 */
import { queryRows } from "../lib/db.js";
import { cachedRows } from "./classification-cache.js";
import { runPythonModule } from "./py-runner.service.js";
import { LABEL_CODE } from "./label-codes.js";
import type { QueryResultRow } from "pg";

/** Mirrors analysis.analysis_insights.__main__ MARKER. */
const INSIGHT_MARKER = "@@INSIGHT_METRICS@@";

export interface InsightSpecSource {
  kind: "table" | "compute";
  table?: string;
  value?: string;
  date?: string;
  code?: string;
  sec_type?: string;
  match?: Record<string, string | number>;
  order?: { by?: "date" | "value"; dir?: "asc" | "desc" };
  fn?: string;
  args?: Record<string, string | number>;
}

export interface InsightSpec {
  scope?: "security" | "industry" | "market";
  page_title?: string;
  /** false = the metric is categorical (trend states, 0/1 flags) — the
   *  resolvers must not stamp a rarity percentile on it. */
  rank?: boolean;
  source: InsightSpecSource;
  compute?: { fn?: string; args?: Record<string, string | number> };
  sec_types?: string[];
}

export interface InsightTemplateRow extends QueryResultRow {
  template_id: string;
  tab: string;
  page: string;
  metric: string;
  label: string;
  unit: string | null;
  description: string | null;
  spec: InsightSpec;
  ordinal: number;
}

export interface InsightMetricValue {
  template_id: string;
  metric: string;
  label: string;
  unit: string;
  description: string;
  scope: string;
  value: number | string | boolean | null;
  value_date: string | null;
  prev_value: number | string | boolean | null;
  delta: number | null;
  /** Rarity: the value's percentile within its PRIOR trailing history —
   *  the share of the metric's own prior observations (same scope filters,
   *  10y window, one row per date) at or below this value; 0.95 = the top
   *  5% tail. NULL for non-numeric values, thin history (< RANK_MIN_N), or
   *  spec `"rank": false` (categorical codes). pct_rank_n = the prior
   *  sample size behind it. */
  pct_rank: number | null;
  pct_rank_n: number | null;
  source_kind: string;
  computed: boolean;
  unresolved: string | null;
}

/** Prior-history window + sample floor for the rarity rank (the repo's
 *  per-code trailing-tail convention; ≥120 = the ohlc-gap flags' floor). */
const RANK_WINDOW_YEARS = 10;
const RANK_MIN_N = 120;

/** 'YYYY-MM-DD' shifted by whole years (leap-safe via UTC). */
function shiftYears(iso: string, years: number): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const dt = new Date(Date.UTC(y + years, m - 1, d));
  return dt.toISOString().slice(0, 10);
}

export interface InsightResolveResult {
  tab: string;
  page: string;
  code: string | null;
  sec_type: string | null;
  industry: string | null;
  as_of: string | null;
  metrics: InsightMetricValue[];
}

/** All registry rows (10-min TTL — they change only on a seed run). */
export function listInsightTemplates(): Promise<InsightTemplateRow[]> {
  return cachedRows("analysis-insights:templates", async () =>
    queryRows<InsightTemplateRow>(
      `SELECT template_id, tab, page, metric, label, unit, description, spec, ordinal
         FROM analysis_insights.insight_templates
        ORDER BY tab, page, ordinal`,
    ),
  );
}

/** Quote one identifier; a schema-qualified name quotes each part. */
function q(name: string): string {
  return name
    .split(".")
    .map((part) => `"${part.replace(/"/g, '""')}"`)
    .join(".");
}

/** 'YYYY-MM' → its last day (clamped to today); 'YYYY-MM-DD' → itself. */
export function normalizeAsOf(raw: string | undefined): string | undefined {
  const text = (raw ?? "").trim();
  if (!text) return undefined;
  if (/^\d{4}-\d{2}$/.test(text)) {
    const [y, m] = [parseInt(text.slice(0, 4), 10), parseInt(text.slice(5, 7), 10)];
    const last = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
    const today = new Date().toISOString().slice(0, 10);
    return last > today ? today : last;
  }
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : undefined;
}

interface ResolveCtx {
  code: string | null;
  secType: string | null;
  industry: string | null;
  asOf: string | null;
}

/** One table-kind metric per the shared contract (see file docstring). */
async function resolveTableMetric(
  source: InsightSpecSource,
  ctx: ResolveCtx,
  scope: string,
  rank = true,
): Promise<Partial<InsightMetricValue> | { unresolved: string }> {
  if (!source.table || !source.value) {
    return { unresolved: "bad table source" };
  }
  const table = q(source.table);
  const dateCol = q(source.date ?? "date");
  const valueCol = q(source.value);

  // One shared positional-args pool: every predicate's placeholder indexes
  // into it, so the value query and the rarity-rank query reuse the same
  // scope filters with different date bounds (as-of vs strictly-prior).
  const args: (string | number)[] = [];
  const pred = (p: string, arg: string | number) =>
    p.replace("?", `$${args.push(arg)}`);

  const filterPreds: string[] = [];
  if (source.code) {
    const code = ctx.code ?? ctx.industry;
    if (!code) return { unresolved: "needs a security/industry" };
    filterPreds.push(pred(`${q(source.code)} = ?`, code));
  }
  const secCol = source.sec_type;
  let secType = ctx.secType;
  if (!secType && scope === "industry") secType = "industry";
  if (secCol) {
    const code = secType ? LABEL_CODE.sec_type[secType as "etf"] : undefined;
    if (code === undefined) return { unresolved: "needs a sec_type" };
    filterPreds.push(pred(`${q(secCol)} = ?`, code));
  }
  const latestDims: string[] = [];
  for (const [key, val] of Object.entries(source.match ?? {})) {
    if (val === "$latest") latestDims.push(key);
    else filterPreds.push(pred(`${q(key)} = ?`, val as string | number));
  }
  const asOfPred = ctx.asOf ? pred(`${dateCol} <= ?`, ctx.asOf) : null;
  const whereFor = (extra: string[] = []) => [
    ...(asOfPred ? [asOfPred] : []),
    ...filterPreds,
    ...extra,
  ];
  for (const key of latestDims) {
    let sql = `SELECT MAX(${q(key)}) AS m FROM ${table}`;
    const maxWhere = whereFor();
    if (maxWhere.length) sql += ` WHERE ${maxWhere.join(" AND ")}`;
    const rows = await queryRows<{ m: string | number | null }>(sql, args);
    if (rows.length === 0 || rows[0].m === null) {
      return { unresolved: `no rows to pick $latest ${key}` };
    }
    filterPreds.push(pred(`${q(key)} = ?`, rows[0].m));
  }
  const order = source.order ?? {};
  const orderAlias = order.by === "value" ? "v" : "d";
  const dir = order.dir === "asc" ? "ASC" : "DESC";
  const valueWhere = whereFor();
  const sql =
    `SELECT d, v FROM (` +
    `  SELECT ${dateCol} AS d, ${valueCol} AS v,` +
    `         ROW_NUMBER() OVER (PARTITION BY ${dateCol}` +
    `                            ORDER BY ${valueCol} DESC NULLS LAST) AS rn` +
    `  FROM ${table}` +
    (valueWhere.length ? ` WHERE ${valueWhere.join(" AND ")}` : "") +
    `) t WHERE rn = 1 AND v IS NOT NULL` +
    ` ORDER BY ${orderAlias} ${dir} NULLS LAST, d DESC LIMIT 2`;
  const rows = await queryRows<{ d: string; v: string | number | null }>(sql, args);
  if (rows.length === 0) return { unresolved: "no row at/before the as-of date" };
  const value = Number(rows[0].v);
  const out: Partial<InsightMetricValue> = {
    value: Number.isFinite(value) ? value : String(rows[0].v),
    value_date: rows[0].d ? String(rows[0].d).slice(0, 10) : null,
  };
  if (rows.length > 1 && rows[1].d !== rows[0].d) {
    const prevNum = Number(rows[1].v);
    const prev = Number.isFinite(prevNum) ? prevNum : String(rows[1].v);
    out.prev_value = prev;
    if (typeof out.value === "number" && typeof prev === "number") {
      out.delta = out.value - prev;
    }
  }

  // Rarity rank: the value's percentile within its PRIOR trailing history
  // (same scope filters, one row per date, the 10y window ending at the
  // value's own date). Skip non-numeric values and spec rank:false
  // (categorical codes — trend states, 0/1 flags — wear no quantile).
  if (rank && typeof out.value === "number" && out.value_date) {
    const lePred = pred(`v <= ?`, out.value);
    const rankPreds = whereFor([
      pred(`${dateCol} < ?`, out.value_date),
      pred(`${dateCol} >= ?`, shiftYears(out.value_date, -RANK_WINDOW_YEARS)),
    ]);
    const rankSql =
      `SELECT AVG(CASE WHEN ${lePred} THEN 1.0 ELSE 0.0 END) AS pct_rank,` +
      ` COUNT(*)::int AS n FROM (` +
      `  SELECT ${dateCol} AS d, ${valueCol} AS v,` +
      `         ROW_NUMBER() OVER (PARTITION BY ${dateCol}` +
      `                            ORDER BY ${valueCol} DESC NULLS LAST) AS rn` +
      `  FROM ${table} WHERE ${rankPreds.join(" AND ")}` +
      `) t WHERE rn = 1 AND v IS NOT NULL`;
    const rankRows = await queryRows<{
      pct_rank: string | number | null;
      n: number;
    }>(rankSql, args);
    const n = rankRows.length ? Number(rankRows[0].n) : 0;
    if (n >= RANK_MIN_N) {
      const r = Number(rankRows[0].pct_rank);
      out.pct_rank = Number.isFinite(r) ? r : null;
      out.pct_rank_n = n;
    }
  }
  return out;
}

/** Compute metrics + fallbacks run in the python resolver (spawned). */
async function resolveViaPython(
  tab: string,
  page: string,
  ctx: ResolveCtx,
  only: string[],
): Promise<Map<string, InsightMetricValue> | null> {
  const args = ["resolve", "--tab", tab, "--page", page];
  if (ctx.code) args.push("--code", ctx.code);
  if (ctx.secType) args.push("--sec-type", ctx.secType);
  if (ctx.industry) args.push("--industry", ctx.industry);
  if (ctx.asOf) args.push("--date", ctx.asOf);
  args.push("--only", only.join(","), "--marker");
  const result = await runPythonModule("analysis.analysis_insights", args, {
    // unique per request — concurrent resolves must not dedupe-drop
    processIdTag: `analysis-insights:${tab}.${page}:${Date.now()}:${only.length}`,
  });
  if (result.already_running || !result.success) return null;
  const idx = result.stdout.lastIndexOf(INSIGHT_MARKER);
  if (idx < 0) return null;
  try {
    const parsed = JSON.parse(result.stdout.slice(idx + INSIGHT_MARKER.length)) as {
      metrics?: InsightMetricValue[];
    };
    return new Map((parsed.metrics ?? []).map((m) => [m.metric, m]));
  } catch {
    return null;
  }
}

/** Resolve one page's metrics for an ask context (never throws). */
export async function resolveInsightMetrics(params: {
  tab: string;
  page: string;
  code?: string;
  secType?: string;
  industry?: string;
  date?: string;
}): Promise<InsightResolveResult> {
  const ctx: ResolveCtx = {
    code: (params.code ?? "").trim() || null,
    secType: (params.secType ?? "").trim() || null,
    industry: (params.industry ?? "").trim() || null,
    asOf: normalizeAsOf(params.date),
  };
  const templates = (await listInsightTemplates()).filter(
    (t) => t.tab === params.tab && t.page === params.page,
  );
  const metrics: InsightMetricValue[] = templates.map((tpl) => ({
    template_id: tpl.template_id,
    metric: tpl.metric,
    label: tpl.label,
    unit: tpl.unit ?? "",
    description: tpl.description ?? "",
    scope: tpl.spec?.scope ?? "security",
    value: null,
    value_date: null,
    prev_value: null,
    delta: null,
    pct_rank: null,
    pct_rank_n: null,
    source_kind: tpl.spec?.source?.kind ?? "table",
    computed: false,
    unresolved: null,
  }));
  const byMetric = new Map(metrics.map((m) => [m.metric, m]));

  // 1 — table metrics resolve in-process (spec-driven SQL fast path)
  const needPython: string[] = [];
  for (const tpl of templates) {
    const out = byMetric.get(tpl.metric)!;
    const spec = tpl.spec ?? { source: { kind: "table" } as InsightSpecSource };
    const labels = spec.sec_types;
    if (labels && labels.length && (!ctx.secType || !labels.includes(ctx.secType))) {
      out.unresolved = `not applicable to sec_type ${ctx.secType ?? "—"}`;
      continue;
    }
    if (spec.source.kind !== "table") {
      needPython.push(tpl.metric);
      continue;
    }
    try {
      const found = await resolveTableMetric(
        spec.source, ctx, spec.scope ?? "security",
        spec.rank !== false,
      );
      if ("unresolved" in found && found.unresolved) {
        if (spec.compute?.fn) needPython.push(tpl.metric);
        else out.unresolved = found.unresolved;
      } else {
        Object.assign(out, found);
      }
    } catch (err) {
      if (spec.compute?.fn) needPython.push(tpl.metric);
      else out.unresolved = `resolver error: ${String(err)}`;
    }
  }

  // 2 — compute metrics / fallbacks via the python resolver (on demand)
  if (needPython.length > 0) {
    const resolved = await resolveViaPython(
      params.tab, params.page, ctx, needPython);
    if (resolved) {
      for (const metric of needPython) {
        const found = resolved.get(metric);
        if (found && found.value !== null && found.value !== undefined) {
          Object.assign(byMetric.get(metric)!, found, { computed: true });
        } else if (!byMetric.get(metric)!.unresolved) {
          byMetric.get(metric)!.unresolved =
            found?.unresolved ?? "on-demand compute unavailable";
        }
      }
    } else {
      for (const metric of needPython) {
        if (!byMetric.get(metric)!.unresolved) {
          byMetric.get(metric)!.unresolved = "on-demand compute unavailable";
        }
      }
    }
  }

  return {
    tab: params.tab,
    page: params.page,
    code: ctx.code,
    sec_type: ctx.secType,
    industry: ctx.industry,
    as_of: ctx.asOf,
    metrics,
  };
}
