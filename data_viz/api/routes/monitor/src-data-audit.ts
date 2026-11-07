/**
 * GET /src-data-audit — per-security source-data coverage audit with the
 * live last_date re-derivation from the stats source tables.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  queryRows,
} from "../../services/db.service.js";
import { querySqlView, renderSqlView, sqlFrag } from "@api/lib/sql-views";
import { decodeLabel, encodeLabelParam, LABEL_CODE } from "../../services/label-codes.js";

const router = Router();

/** Live last_date re-derivation per served type. The audit table stores
 *  coverage as of the last build, so its last_date goes stale the moment
 *  new source data lands; each CTE below re-checks the latest data per
 *  security code from the SAME stats tables the writers count:
 *    index → stats.index_identity (plain identity MAX — the index writer's
 *            coverage source),
 *    etf   → stats.etf_identity   (plain identity MAX — the ETF build's
 *            fetch_per_code_day_stats source),
 *    stock → stats.stock_basic_stats WHERE close IS NOT NULL — NOT plain
 *            stock_identity: the streamer writes an identity row per
 *            listed stock per trading day even pre-open / suspended (no
 *            quote), which would overstate freshness (~20% of codes).
 *  Only the SERVED types' CTEs are built (the stock one scans the full
 *  basic_stats table), so a filtered fetch never pays for the others. */
const AUDIT_TYPES = ["index", "etf", "stock"] as const;
type AuditType = (typeof AUDIT_TYPES)[number];

const AUDIT_LIVE_LAST_DATE: Record<
  AuditType,
  { cte: string; cteName: string; typeCode: number }
> = {
  index: {
    cte: `li AS (SELECT code, MAX(date)::text AS last_date
                   FROM stats.index_identity GROUP BY code)`,
    cteName: "li",
    typeCode: LABEL_CODE.sec_type.index,
  },
  etf: {
    cte: `le AS (SELECT code, MAX(date)::text AS last_date
                   FROM stats.etf_identity GROUP BY code)`,
    cteName: "le",
    typeCode: LABEL_CODE.sec_type.etf,
  },
  stock: {
    cte: `ls AS (SELECT code, MAX(date)::text AS last_date
                   FROM stats.stock_basic_stats
                  WHERE close IS NOT NULL GROUP BY code)`,
    cteName: "ls",
    typeCode: LABEL_CODE.sec_type.stock,
  },
};

/** GET /api/monitor/src-data-audit?sec_type=index|etf|stock|all&q=&limit=
 *  — the per-security source-data coverage audit
 *  (monitor.src_data_audit: n_days / first_date per (sec_type, code),
 *  written by the classification + ETF builds).
 *  last_date is re-derived per code at query time from the stats source
 *  tables (see AUDIT_LIVE_LAST_DATE), so every refresh reflects the data
 *  that actually arrived; codes with no live rows (JSON-only indices)
 *  fall back to the stored audit value.
 *  sec_type filters the SMALLINT label-code column (domain 'sec_type' —
 *  the param is encoded via the label-code contract, rows decode at this
 *  boundary); q narrows by code substring or display name (name JOINed in
 *  from stats.sec_classification — one row per code, primary-parent row
 *  preferred). Ordered sec_type, code for a stable listing; per-type row
 *  counts (decoded keys) ride along for the header. */
interface SrcDataAuditRow {
  sec_type: number;
  code: string;
  exchange: string | null;
  board: number | null;
  n_days: number;
  first_date: string | null;
  last_date: string | null;
  updated_at: Date;
  name: string | null;
}

export interface SrcDataAuditEntry {
  secType: string;
  code: string;
  name: string | null;
  exchange: string | null;
  /** Decoded board label (board label-code domain): MAIN | STAR | GEM |
   *  BSE — stock rows only, null for index/etf. */
  board: string | null;
  nDays: number;
  firstDate: string | null;
  lastDate: string | null;
  updatedAt: string;
}

export interface SrcDataAuditResponse {
  success: true;
  generatedAt: string;
  /** Total rows per sec_type (decoded label keys, unfiltered — the
   *  header's counts). */
  counts: Record<string, number>;
  rows: SrcDataAuditEntry[];
}

router.get("/src-data-audit", async (req: Request, res: Response) => {
  const secTypeRaw = typeof req.query.sec_type === "string" ? req.query.sec_type.trim() : "";
  const q = typeof req.query.q === "string" ? req.query.q.trim() : "";
  // Cap sits above the full universe (~20k rows) so the UI can fetch one
  // sec_type COMPLETE — a "check" table that silently truncates is useless.
  const limit = Math.min(Math.max(Number(req.query.limit) || 20000, 1), 20000);
  // "all" (or omitted) → no sec_type filter; anything else must decode to
  // a 'sec_type' label code — a bad value is a 400, not a silent "all".
  const secTypeCode =
    secTypeRaw === "all" || secTypeRaw === ""
      ? null
      : encodeLabelParam("sec_type", secTypeRaw);
  if (secTypeRaw !== "all" && secTypeRaw !== "" && secTypeCode === null) {
    res.status(400).json({
      success: false,
      error: "query param 'sec_type' must be index | etf | stock | all",
    });
    return;
  }
  try {
    // queryRows routes through the replica-lag probe + primary failover —
    // a rebuilding / replaying replica must 500 this page.
    const countsRows = await querySqlView<{ sec_type: number; n: string }>(
      "data_viz/api/routes/monitor/src_audit_counts",
    );
    const counts: Record<string, number> = {};
    for (const r of countsRows) {
      const label = decodeLabel("sec_type", r.sec_type);
      if (label != null) counts[label] = Number(r.n);
    }
    // Live last_date CTEs for the served types only ($5.. guard each join
    // to its own sec_type — bare 6-digit codes exist in BOTH the index and
    // ETF identity tables, so an unguarded join could cross-match).
    const servedTypes: AuditType[] =
      secTypeRaw === "all" || secTypeRaw === ""
        ? [...AUDIT_TYPES]
        : [secTypeRaw as AuditType];
    // Live last_date CTEs for the served types only ($5.. guard each join
    // to its own sec_type — bare 6-digit codes exist in BOTH the index and
    // ETF identity tables, so an unguarded join could cross-match). The
    // per-type CTEs / joins / COALESCE list ride fragments of the sql view
    // data_viz/api/routes/monitor/src_audit_rows — their positional params
    // ($5..) are variable-arity, so the params stay caller-assembled
    // ($1..$4 are the route's own filter/limit params, bound by the fixed
    // template body).
    const liveCtes = servedTypes
      .map((t) => AUDIT_LIVE_LAST_DATE[t].cte)
      .join(", ");
    const liveJoins = servedTypes
      .map((t, i) => {
        const live = AUDIT_LIVE_LAST_DATE[t];
        return `LEFT JOIN ${live.cteName}
                 ON ${live.cteName}.code = a.code
                AND ${live.cteName}.last_date IS NOT NULL
                AND a.sec_type = $${5 + i}`;
      })
      .join("\n       ");
    const lastDateExpr = `COALESCE(${servedTypes
      .map((t) => `${AUDIT_LIVE_LAST_DATE[t].cteName}.last_date`)
      .join(", ")}, a.last_date::text)`;
    const rows = await queryRows<SrcDataAuditRow>(
      renderSqlView("data_viz/api/routes/monitor/src_audit_rows", {
        live_ctes: sqlFrag(liveCtes),
        last_date_expr: sqlFrag(lastDateExpr),
        live_joins: sqlFrag(liveJoins),
      }).sql,
      [
        secTypeCode,
        secTypeRaw === "all" || secTypeRaw === "" ? null : secTypeRaw,
        q,
        limit,
        ...servedTypes.map((t) => AUDIT_LIVE_LAST_DATE[t].typeCode),
      ],
    );
    const body: SrcDataAuditResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      counts,
      rows: rows.map((r) => ({
        secType: decodeLabel("sec_type", r.sec_type) ?? String(r.sec_type),
        code: r.code,
        name: r.name,
        exchange: r.exchange,
        board: decodeLabel("board", r.board),
        nDays: r.n_days,
        firstDate: r.first_date,
        lastDate: r.last_date,
        updatedAt: r.updated_at.toISOString(),
      })),
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/src-data-audit] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
