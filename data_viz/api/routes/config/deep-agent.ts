/**
 * GET/PUT /api/config/deep-agent — the AI page's Deep-agent run limits
 * (deep_agent_config.json, ONE file).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  readDeepAgentLimits,
  writeDeepAgentLimits,
  type DeepAgentLimitsEntry,
} from "../../services/static-config.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Deep agent run limits — deep_agent_config.json (ONE file)
// ---------------------------------------------------------------------------

/** The deep agent's run limits in camelCase (the UI payload shape);
 *  the file keeps the snake_case mirror of
 *  llm_agents/deep_agents/limits.py::RunLimits. */
export interface DeepAgentLimitsPayload {
  maxConversationSteps: number;
  minConversationSteps: number;
  maxStepsCap: number;
  recursionLimit: number;
  runTimeoutSec: number;
  llmRequestTimeoutSec: number;
  maxPlanNodes: number;
  maxDelegatedSteps: number;
  planRepairRounds: number;
  planProposalBatches: number;
  maxTotalPlanSteps: number;
  maxForkChildren: number;
  maxParallelDelegatedSteps: number;
  maxConcurrentSubagents: number;
  maxSpawnDepth: number;
  subagentTimeoutSec: number | null;
  maxOutputTokens: number;
  maxOutputRepairs: number;
  findingMaxChars: number;
  findingsBlockMaxChars: number;
  promptEagerRows: number;
  promptEagerChars: number;
  payloadGuardChars: number;
  payloadEntryMaxChars: number;
  payloadPreviewChars: number;
  payloadPreviewItems: number;
  toolResultMaxChars: number;
  /** The supervisor repair lane's knobs (see
   *  static-config.service's entry). */
  supervisorPollIntervalSec: number;
  supervisorRepairGraceSec: number;
  supervisorMaxNodeRetries: number;
  supervisorMaxRepairsPerRun: number;
  supervisorRetryThrottleSec: number;
  notes: string | null;
  sourceRef: string | null;
  updatedAt: string;
}

function toLimitsPayload(e: DeepAgentLimitsEntry): DeepAgentLimitsPayload {
  return {
    maxConversationSteps: e.max_conversation_steps,
    minConversationSteps: e.min_conversation_steps,
    maxStepsCap: e.max_steps_cap,
    recursionLimit: e.recursion_limit,
    runTimeoutSec: e.run_timeout_sec,
    llmRequestTimeoutSec: e.llm_request_timeout_sec,
    maxPlanNodes: e.max_plan_nodes,
    maxDelegatedSteps: e.max_delegated_steps,
    planRepairRounds: e.plan_repair_rounds,
    planProposalBatches: e.plan_proposal_batches,
    maxTotalPlanSteps: e.max_total_plan_steps,
    maxForkChildren: e.max_fork_children,
    maxParallelDelegatedSteps: e.max_parallel_delegated_steps,
    maxConcurrentSubagents: e.max_concurrent_subagents,
    maxSpawnDepth: e.max_spawn_depth,
    subagentTimeoutSec: e.subagent_timeout_sec,
    maxOutputTokens: e.max_output_tokens,
    maxOutputRepairs: e.max_output_repairs,
    findingMaxChars: e.finding_max_chars,
    findingsBlockMaxChars: e.findings_block_max_chars,
    promptEagerRows: e.prompt_eager_rows,
    promptEagerChars: e.prompt_eager_chars,
    payloadGuardChars: e.payload_guard_chars,
    payloadEntryMaxChars: e.payload_entry_max_chars,
    payloadPreviewChars: e.payload_preview_chars,
    payloadPreviewItems: e.payload_preview_items,
    toolResultMaxChars: e.tool_result_max_chars,
    supervisorPollIntervalSec: e.supervisor_poll_interval_sec,
    supervisorRepairGraceSec: e.supervisor_repair_grace_sec,
    supervisorMaxNodeRetries: e.supervisor_max_node_retries,
    supervisorMaxRepairsPerRun: e.supervisor_max_repairs_per_run,
    supervisorRetryThrottleSec: e.supervisor_retry_throttle_sec,
    notes: e.notes,
    sourceRef: e.source_ref,
    updatedAt: e.updated_at,
  };
}

/** The same per-field specs as static-config.service's table, keyed by
 *  the camelCase payload names — the PUT's validation: every field must
 *  arrive a finite number inside the range (400 otherwise, never a
 *  silent clamp — the UI's number inputs already bound the ranges). */
const LIMITS_PAYLOAD_FIELDS: Array<{
  key: keyof Omit<DeepAgentLimitsPayload, "notes" | "sourceRef" | "updatedAt">;
  snake: keyof Omit<DeepAgentLimitsEntry, "notes" | "source_ref" | "updated_at">;
  min: number;
  max: number | null;
  nullable?: boolean;
}> = [
  { key: "maxConversationSteps", snake: "max_conversation_steps", min: 1, max: 1000 },
  { key: "minConversationSteps", snake: "min_conversation_steps", min: 1, max: 64 },
  { key: "maxStepsCap", snake: "max_steps_cap", min: 1, max: 1000 },
  { key: "recursionLimit", snake: "recursion_limit", min: 25, max: 100000 },
  { key: "runTimeoutSec", snake: "run_timeout_sec", min: 60, max: null },
  { key: "llmRequestTimeoutSec", snake: "llm_request_timeout_sec", min: 5, max: null },
  { key: "maxPlanNodes", snake: "max_plan_nodes", min: 1, max: 200 },
  { key: "maxDelegatedSteps", snake: "max_delegated_steps", min: 0, max: 200 },
  { key: "planRepairRounds", snake: "plan_repair_rounds", min: 0, max: 10 },
  { key: "planProposalBatches", snake: "plan_proposal_batches", min: 1, max: 24 },
  { key: "maxTotalPlanSteps", snake: "max_total_plan_steps", min: 1, max: 1000 },
  { key: "maxForkChildren", snake: "max_fork_children", min: 0, max: 200 },
  { key: "maxParallelDelegatedSteps", snake: "max_parallel_delegated_steps", min: 1, max: 64 },
  { key: "maxConcurrentSubagents", snake: "max_concurrent_subagents", min: 1, max: 64 },
  { key: "maxSpawnDepth", snake: "max_spawn_depth", min: 0, max: 8 },
  { key: "subagentTimeoutSec", snake: "subagent_timeout_sec", min: 1, max: null, nullable: true },
  { key: "maxOutputTokens", snake: "max_output_tokens", min: 256, max: null },
  { key: "maxOutputRepairs", snake: "max_output_repairs", min: 0, max: 5 },
  { key: "findingMaxChars", snake: "finding_max_chars", min: 50, max: null },
  { key: "findingsBlockMaxChars", snake: "findings_block_max_chars", min: 100, max: null },
  { key: "promptEagerRows", snake: "prompt_eager_rows", min: 0, max: 100 },
  { key: "promptEagerChars", snake: "prompt_eager_chars", min: 0, max: null },
  { key: "payloadGuardChars", snake: "payload_guard_chars", min: 0, max: null },
  { key: "payloadEntryMaxChars", snake: "payload_entry_max_chars", min: 200, max: null },
  { key: "payloadPreviewChars", snake: "payload_preview_chars", min: 100, max: null },
  { key: "payloadPreviewItems", snake: "payload_preview_items", min: 1, max: 50 },
  { key: "toolResultMaxChars", snake: "tool_result_max_chars", min: 500, max: null },
  { key: "supervisorPollIntervalSec", snake: "supervisor_poll_interval_sec", min: 2, max: 60 },
  { key: "supervisorRepairGraceSec", snake: "supervisor_repair_grace_sec", min: 0, max: 600 },
  { key: "supervisorMaxNodeRetries", snake: "supervisor_max_node_retries", min: 0, max: 3 },
  { key: "supervisorMaxRepairsPerRun", snake: "supervisor_max_repairs_per_run", min: 0, max: 6 },
  { key: "supervisorRetryThrottleSec", snake: "supervisor_retry_throttle_sec", min: 0, max: 300 },
];

/** GET /api/config/deep-agent — the current run limits (defaults served
 *  for a missing/unparseable file). */
router.get("/deep-agent", (_req: Request, res: Response) => {
  try {
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      limits: toLimitsPayload(readDeepAgentLimits()),
    });
  } catch (err) {
    console.error("[config/deep-agent GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** PUT /api/config/deep-agent — write the run limits (updated_at
 *  stamped server-side). Takes effect on the NEXT deep-agent run — the
 *  daemon re-reads the file per run. */
router.put("/deep-agent", (req: Request, res: Response) => {
  try {
    const b = (typeof req.body === "object" && req.body != null
      ? req.body
      : {}) as Record<string, unknown>;
    const entry = { updated_at: "" } as DeepAgentLimitsEntry;
    for (const spec of LIMITS_PAYLOAD_FIELDS) {
      const value = b[spec.key];
      if (spec.nullable && (value === null || value === "")) {
        entry[spec.snake] = null;
        continue;
      }
      if (
        typeof value !== "number" || !Number.isFinite(value) ||
        value < spec.min || (spec.max !== null && value > spec.max)
      ) {
        throw new Error(
          `${String(spec.key)} must be a number in [${spec.min}, ` +
          `${spec.max ?? "∞"}]`,
        );
      }
      entry[spec.snake] = value;
    }
    entry.notes = typeof b.notes === "string" ? b.notes : null;
    entry.source_ref =
      typeof b.sourceRef === "string" ? b.sourceRef : null;
    const saved = writeDeepAgentLimits(entry);
    res.json({ success: true, limits: toLimitsPayload(saved) });
  } catch (err) {
    console.error("[config/deep-agent PUT] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

export default router;
