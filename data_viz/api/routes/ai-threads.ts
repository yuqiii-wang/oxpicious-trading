/**
 * AI thread routes — the deep-agent Thread Graph view's read endpoints.
 * Mounted under /api/ai alongside routes/ai/ (app.ts registers both
 * routers on the prefix; a request falls through to this router when
 * ai.ts has no match). The thread LIST is the existing summary-runs
 * endpoint (GET /api/ai/summary-runs — one row per planned thread);
 * this router adds the per-thread detail the graph polls:
 *
 *   GET /api/ai/threads/:threadId
 *     The thread's full node graph — every llm.deep_agent_states row
 *     (top-level plan steps + spawned-subagent child states) and every
 *     llm.deep_agent_contexts payload (plan JSON, step input/result
 *     notes, subagent answers). 404 for an unknown/malformed id (no
 *     states). See services/ai-thread.service.ts for the shape.
 *
 *   GET /api/ai/threads/:threadId/states/:stateId/llm-requests
 *     One task's recorded LLM requests/responses (the task-row
 *     expansion's lazy fetch — payloads are big, so they are not in the
 *     thread detail). Attribution: chain_id = the task's llm_context_id,
 *     a top-level step narrowed to its tick window (see the service).
 *     404 unknown thread/state, 400 a non-integer stateId.
 */
import { Router, type Request, type Response } from "express";
import {
  getAgentThreadDetail,
  getAgentStateLlmRequests,
} from "../services/ai-thread.service.js";

const router = Router();

router.get("/threads/:threadId", async (req: Request, res: Response) => {
  try {
    const detail = await getAgentThreadDetail(String(req.params.threadId));
    if (detail === null) {
      res.status(404).json({
        error: `no deep-agent thread '${String(req.params.threadId)}'`,
      });
      return;
    }
    res.json(detail);
  } catch (err) {
    console.error("[ai/threads] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get(
  "/threads/:threadId/states/:stateId/llm-requests",
  async (req: Request, res: Response) => {
    const stateId = Number(req.params.stateId);
    if (!Number.isInteger(stateId)) {
      res.status(400).json({ error: "stateId must be an integer" });
      return;
    }
    try {
      const data = await getAgentStateLlmRequests(
        String(req.params.threadId),
        stateId,
      );
      if (data === null) {
        res.status(404).json({
          error: `no task ${stateId} on thread '${String(req.params.threadId)}'`,
        });
        return;
      }
      res.json(data);
    } catch (err) {
      console.error("[ai/threads] llm-requests error:", err);
      res.status(500).json({ error: String(err) });
    }
  },
);

export default router;
