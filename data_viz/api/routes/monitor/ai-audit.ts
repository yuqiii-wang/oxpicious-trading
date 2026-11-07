/**
 * GET /ai/usage + GET /ai/kg-queries + GET /ai/kg-chain — the AI tab's
 * LLM-request audit and the knowledge-graph query log / chain subgraph.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getReadPool,
} from "../../services/db.service.js";
import { renderSqlView } from "@api/lib/sql-views";
import { getKgChain, isKgNamespace } from "../../services/kg.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  AI usage — GET /ai/usage?days=30
//  The AI tab's LLM-request audit: llm.llm_requests aggregated per
//  (service, model, GMT date) over a trailing window — request / failed
//  counts, the usage-block token sums (input / output / total, extracted
//  at write time by llm_agents/_core/request_log so no JSONB parsing
//  here) and whole-call durations. Live SQL (no computed table): the
//  window is days × services × models rows, small by construction, and
//  the ix_llm_requests_date index bounds the scan. The deep-agent runs
//  half of the tab reads the EXISTING /api/ai endpoints (summary-runs +
//  summary/active) — the run history is run-lifecycle data, not an
//  aggregate, and re-serving it under /monitor would duplicate it.
// ---------------------------------------------------------------------------

interface AiUsageRow {
  service: string;
  model: string | null;
  date: string;
  request_count: number;
  failed_count: number;
  input_tokens: string | number;
  output_tokens: string | number;
  total_tokens: string | number;
  avg_duration_ms: number | null;
  max_duration_ms: number | null;
}

export interface AiUsageEntry {
  service: string;
  /** The chat model when the request recorded one (null = none / a
   *  web-search POST without a model). */
  model: string | null;
  /** GMT operational day (Beijing 08:00 boundary — the table's own
   *  generated date column). */
  date: string;
  requestCount: number;
  failedCount: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  avgDurationMs: number | null;
  maxDurationMs: number | null;
}

export interface AiUsageResponse {
  success: true;
  generatedAt: string;
  /** Distinct sorted dates present in the window (trend x axis). */
  dates: string[];
  /** Distinct sorted services present in the window (trend series). */
  services: string[];
  rows: AiUsageEntry[];
}

router.get("/ai/usage", async (req: Request, res: Response) => {
  const days = Math.min(Math.max(Number(req.query.days) || 30, 1), 365);
  try {
    const pool = getReadPool();
    const { sql, params } = renderSqlView(
      "data_viz/api/routes/monitor/ai_usage_agg",
      { days },
    );
    const agg = await pool.query<AiUsageRow>(sql, params);
    const rows: AiUsageEntry[] = agg.rows.map((r) => ({
      service: r.service,
      model: r.model,
      date: r.date,
      requestCount: r.request_count,
      failedCount: r.failed_count,
      inputTokens: Number(r.input_tokens),
      outputTokens: Number(r.output_tokens),
      totalTokens: Number(r.total_tokens),
      avgDurationMs: r.avg_duration_ms == null ? null : Number(r.avg_duration_ms),
      maxDurationMs: r.max_duration_ms,
    }));
    const body: AiUsageResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      dates: [...new Set(rows.map((r) => r.date))].sort(),
      services: [...new Set(rows.map((r) => r.service))].sort(),
      rows,
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/ai/usage] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

// ---------------------------------------------------------------------------
//  AI knowledge-graph queries — GET /ai/kg-queries?limit=50
//  The AI tab's KG half: the deep agents' kg_search chain cache / log
//  (llm.kg_queries — one chain per (query, namespace), written after
//  every non-empty traversal and replayed on an exact re-ask while the
//  entity-description hash holds). One row per logged CHAIN: edge count,
//  depth span, the validity hash, when logged. Recent-first, read from
//  the replica like the usage aggregate above (monitor may lag a fresh
//  search by the replica's replay delay — the cache itself always reads
//  the primary).
// ---------------------------------------------------------------------------

interface AiKgQueryRow {
  query: string;
  namespace: number;
  chain_id: string;
  edge_count: number;
  min_depth: number;
  max_depth: number;
  entity_hash: string;
  created_at: Date;
}

export interface AiKgQueryEntry {
  query: string;
  /** kg_namespace codes (same vocabulary as llm.kg_entities): 0 DB
   *  semantics, 1 prompts. */
  namespace: number;
  chainId: string;
  edgeCount: number;
  minDepth: number;
  maxDepth: number;
  entityHash: string;
  loggedAt: string;
}

export interface AiKgQueriesResponse {
  success: true;
  generatedAt: string;
  rows: AiKgQueryEntry[];
}

router.get("/ai/kg-queries", async (req: Request, res: Response) => {
  const limit = Math.min(Math.max(Number(req.query.limit) || 50, 1), 500);
  try {
    const pool = getReadPool();
    const { sql, params } = renderSqlView(
      "data_viz/api/routes/monitor/ai_kg_queries",
      { limit },
    );
    const agg = await pool.query<AiKgQueryRow>(sql, params);
    const rows: AiKgQueryEntry[] = agg.rows.map((r) => ({
      query: r.query,
      namespace: r.namespace,
      chainId: r.chain_id,
      edgeCount: r.edge_count,
      minDepth: r.min_depth,
      maxDepth: r.max_depth,
      entityHash: r.entity_hash,
      loggedAt: r.created_at.toISOString(),
    }));
    const body: AiKgQueriesResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      rows,
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/ai/kg-queries] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

//  AI knowledge-graph chain — GET /ai/kg-chain?namespace=N&chain_id=X
//  ONE logged kg_search chain drawn as a subgraph for the AI tab's
//  expandable rows: the chain's edges (depth order) resolved against
//  the live hypergraph plus the member entities as the node set. No
//  cache — the chain log is runtime state (same freshness rule as
//  /ai/kg-queries above).
// ---------------------------------------------------------------------------

export interface AiKgChainResponse {
  success: true;
  generatedAt: string;
  namespace: number;
  chainId: string;
  entities: Array<{
    entityId: number;
    name: string;
    entityType: string;
    description: string | null;
    tags: string[];
  }>;
  edges: Array<{
    edgeId: number;
    relation: string;
    subRelation: string | null;
    weight: number | null;
    members: Array<{ entityId: number; name: string | null; entityType: string | null }>;
  }>;
}

router.get("/ai/kg-chain", async (req: Request, res: Response) => {
  const namespace = Number(req.query.namespace);
  const chainId = String(req.query.chain_id ?? "");
  if (!isKgNamespace(namespace) || !/^[0-9a-f]{8,64}$/i.test(chainId)) {
    res.status(400).json({ success: false, error: "bad namespace or chain_id" });
    return;
  }
  try {
    const chain = await getKgChain(namespace, chainId);
    const body: AiKgChainResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      namespace: chain.namespace,
      chainId: chain.chainId,
      entities: chain.entities,
      edges: chain.edges,
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/ai/kg-chain] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
