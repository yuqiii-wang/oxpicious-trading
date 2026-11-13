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
 *   GET /api/ai/threads/:threadId/states/:stateId/agent-steps
 *     One task's AGENT STEPS (the task-row expansion's lazy fetch) —
 *     replaces the former llm-requests list: step_type 0 inference =
 *     one LLM request (chain attribution as before), step_type 1 spawn =
 *     ONE spawned sub node collapsed (fresh prompt in, final output
 *     out, child-chain token rollup; the sub node's intermediates stay
 *     at the child node). 404 unknown thread/state, 400 a non-integer
 *     stateId.
 */
import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../lib/http.js";
import {
  getAgentThreadDetail,
  getAgentStateAgentSteps,
} from "../services/ai/ai-thread.service.js";

const router = Router();

router.get("/threads/:threadId", wrapAsync(async (req: Request, res: Response) => {
  const detail = await getAgentThreadDetail(String(req.params.threadId));
  if (detail === null) {
    throw HttpError.notFound(`no deep-agent thread '${String(req.params.threadId)}'`);
  }
  res.json(detail);
}));

router.get(
  "/threads/:threadId/states/:stateId/agent-steps",
  wrapAsync(async (req: Request, res: Response) => {
    const stateId = Number(req.params.stateId);
    if (!Number.isInteger(stateId)) {
      throw new HttpError(400, "INVALID_PARAM", "stateId must be an integer");
    }
    const data = await getAgentStateAgentSteps(
      String(req.params.threadId),
      stateId,
    );
    if (data === null) {
      throw HttpError.notFound(`no task ${stateId} on thread '${String(req.params.threadId)}'`);
    }
    res.json(data);
  }),
);

export default router;
