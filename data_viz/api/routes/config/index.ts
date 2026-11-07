// The route-catalog doc comment below is extracted verbatim from
// routes/config.ts — the routes themselves live in this directory's
// subfiles; this file composes them in the original section order.
/**
 * Config API routes — READ + WRITE over the repo's static config trees
 * (the Config page's tabs; mostly JSON files under database/ or
 * scripts/, plus the two READ-only seed-table tabs: Keywords and the
 * Knowledge Graph).
 *
 *   Service tab — database/sql/monitoring/static/service_config/ (one
 *   file per service; default.json is the tree-root fallback). Writes
 *   one entry per call; deleting a service's file lets the fallback
 *   apply again.
 *
 *   External Resources tab — database/sql/monitoring/static/
 *   external_request_config/ (one file per service holding its
 *   request-type rows; default.json fallback = the repo-wide 60s
 *   anti-bot cooldown). Writes one service's WHOLE row list per call
 *   (the dialog edits rows client-side, then saves the service's file);
 *   resource_type crosses the boundary DECODED ("http" | "llm" — the
 *   static.label_dim SMALLINT contract applies inside the files only).
 *
 *   Security Classification tab — database/sec_statics/ (hand-edited
 *   classification_rules/*.json + importance_rules/*.json curated top
 *   ranks + sec_owners.json, plus the GENERATED sec_classification/
 *   shard tree). Raw-text per-file GET/PUT; only files already in the
 *   tree are writable (JSON-validated, verbatim write) — shard creation
 *   stays with builds.classification.
 *
 *   Orchestration tab — READ-ONLY over scripts/orchestration/ (one
 *   step JSON per tier script; see orchestration.service). The files
 *   are git-reviewed edits like every other static config, so the tab
 *   only displays: no write route.
 *
 *   Deep-agent run limits — database/sql/monitoring/static/
 *   deep_agent_config.json (ONE file): the deep agent's LLM-request
 *   budget knobs (conversation steps, recursion, plan-tree caps,
 *   subagent fan-out, per-request output ceiling, findings-bridge and
 *   eager-prompt budgets). The AI page's Run Limits panel reads/writes
 *   these; the harness re-reads the file per run
 *   (llm_agents/deep_agents/limits.py), so a save applies to the next
 *   run with no daemon restart.
 *
 *   Knowledge Graph tab — llm.kg_entities / llm.kg_edges (READ-only,
 *   like the Keywords tab a static seed: rows land only via the SQL
 *   seed files under database/sql/llm/static/knowledge_graphs/). GET
 *   /kg?namespace=N returns ONE namespace's subgraph — the entities
 *   plus their name-resolved hyperedges; the namespace codes mirror the
 *   deep-agent kit's (0 DB semantics | 1 prompts) until kg_namespace
 *   registers in label_dim with the first KG writer.
 *
 *   Secrets tab — static.secret_config (the ONLY DB-backed WRITABLE
 *   config tab): GET/POST/PUT over the CREDENTIAL registry (secret_type
 *   llm | web_search — the provider column's label domain is selected
 *   by the type, the audit pattern) that the Python consumers read at
 *   startup (deep agent per run: llm_agents/deep_agents/llm_config.py;
 *   every provider's key fallback:
 *   llm_agents/_core/secret_config.py — successor of the deleted root
 *   .env keys). The INSERT/UPDATE SQL lives in
 *   database/sql_writes/data_viz/api/secret_config/
 *   (secret-config.service executes it); the API key is encrypted at
 *   rest (AES-256-GCM,
 *   secret-box.service) and every read surfaces only the masked hint —
 *   the ciphertext column is never selected, the plaintext never
 *   logged. POST /secrets/test fires ONE real minimal request per type
 *   (chat ping / provider search) with the form's values as typed.
 *
 * All writes go through the static-config / sec-statics services
 * (updated_at stamping where the file shape has it, canonical shape,
 * atomic temp+rename). There is no auth on this localhost API — the
 * same trust level as POST /api/monitor/health-check spawning Python.
 */

import { Router } from "express";
import serviceRouter from "./service.js";
import extRequestRouter from "./ext-request.js";
import deepAgentRouter from "./deep-agent.js";
import secStaticsRouter from "./sec-statics.js";
import orchestrationRouter from "./orchestration.js";
import keywordMapsRouter from "./keyword-maps.js";
import kgRouter from "./kg.js";
import secretsRouter from "./secrets.js";

const router = Router();

router.use(serviceRouter);
router.use(extRequestRouter);
router.use(deepAgentRouter);
router.use(secStaticsRouter);
router.use(orchestrationRouter);
router.use(keywordMapsRouter);
router.use(kgRouter);
router.use(secretsRouter);

export default router;
