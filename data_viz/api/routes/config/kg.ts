/**
 * GET /api/config/kg — the Knowledge Graph tab (read-only).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  isKgNamespace,
  KG_NAMESPACES,
  listKgGraph,
} from "../../services/kg.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Knowledge Graph tab — llm.kg_entities / llm.kg_edges (READ-only)
// ---------------------------------------------------------------------------

/** GET /api/config/kg?namespace=0 — ONE namespace's subgraph: the
 *  entities plus their name-resolved hyperedges (the kg_namespace codes
 *  live in kg.service's KG_NAMESPACES — deliberately not yet a
 *  label_dim domain). READ-ONLY like the Keywords tab: the graph is a
 *  static seed (rows land only via database/sql/llm/static/
 *  knowledge_graphs/); ?namespace outside the shipped vocabulary is a
 *  400, not an empty graph. */
router.get("/kg", async (req: Request, res: Response) => {
  const namespace: unknown = Number(req.query.namespace ?? 0);
  if (!isKgNamespace(namespace)) {
    res.status(400).json({
      success: false,
      error: `namespace must be one of ${
        KG_NAMESPACES.map((n) => n.code).join(" / ")
      } (${KG_NAMESPACES.map((n) => n.label).join(" / ")})`,
    });
    return;
  }
  try {
    const graph = await listKgGraph(namespace);
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      ...graph,
    });
  } catch (err) {
    console.error("[config/kg GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
