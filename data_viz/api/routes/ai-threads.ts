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
 *
 *   GET /api/ai/threads/:threadId/nodes/:node/agent-steps
 *     One runner-lane node's AGENT STEPS keyed by its node item's chain
 *     (the state-less seats — the output node — have no state row to
 *     hang the state-keyed fetch on). 404 unknown node / no chain.
 *
 *   POST /api/ai/threads/:threadId/states/:stateId/spawn   {goal, max_steps?}
 *   POST /api/ai/threads/:threadId/states/:stateId/fork    {goal, request_id}
 *     The Thread Graph's UI fork/spawn buttons (2026-11-15) — forwarded
 *     to the deep-agents daemon, which registers the child state row
 *     synchronously (the canvas shows it immediately) and runs the
 *     child conversation as a background task. Response = the child's
 *     ids; the UI's detail poll picks the settled row up. 409 on an
 *     active chain, 400 a bad goal / fork request id, 404 unknown
 *     state (all daemon-originated details surface verbatim).
 */
import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../lib/http.js";
import {
  getAgentThreadDetail,
  getAgentStateAgentSteps,
  getAgentNodeAgentSteps,
  runNodeSpawn,
  runNodeSpawnOnNode,
  runNodeFork,
  runNodeForkOnNode,
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

/** One runner-lane node's AGENT STEPS — the state-less seats (the
 *  output node) keyed by their node item's chain (2026-11-15). */
router.get(
  "/threads/:threadId/nodes/:node/agent-steps",
  wrapAsync(async (req: Request, res: Response) => {
    const data = await getAgentNodeAgentSteps(
      String(req.params.threadId),
      String(req.params.node),
    );
    if (data === null) {
      throw HttpError.notFound(
        `no recorded agent steps for node '${String(req.params.node)}' `
        + `on thread '${String(req.params.threadId)}'`,
      );
    }
    res.json(data);
  }),
);

/** POST one UI SPAWN action — forwarded to the deep-agents daemon (the
 *  child runs as a background task there; the response is the child's
 *  ids, the UI polls the detail). */
router.post(
  "/threads/:threadId/states/:stateId/spawn",
  wrapAsync(async (req: Request, res: Response) => {
    const stateId = Number(req.params.stateId);
    if (!Number.isInteger(stateId)) {
      throw new HttpError(400, "INVALID_PARAM", "stateId must be an integer");
    }
    const goal = String((req.body as { goal?: unknown })?.goal ?? "").trim();
    if (goal === "") {
      throw new HttpError(400, "INVALID_PARAM", "goal is required");
    }
    const maxSteps = Number((req.body as { max_steps?: unknown })?.max_steps);
    const result = await runNodeSpawn(
      String(req.params.threadId),
      stateId,
      goal,
      Number.isInteger(maxSteps) && maxSteps > 0 ? maxSteps : undefined,
    );
    res.json(result);
  }),
);

/** POST one RUN-NODE SPAWN action — the state-less runner-lane seats
 *  (the output node) forwarded to the daemon; the child parents under
 *  the chain root's plan state row. */
router.post(
  "/threads/:threadId/nodes/:node/spawn",
  wrapAsync(async (req: Request, res: Response) => {
    const goal = String((req.body as { goal?: unknown })?.goal ?? "").trim();
    if (goal === "") {
      throw new HttpError(400, "INVALID_PARAM", "goal is required");
    }
    const maxSteps = Number((req.body as { max_steps?: unknown })?.max_steps);
    const result = await runNodeSpawnOnNode(
      String(req.params.threadId),
      String(req.params.node),
      goal,
      Number.isInteger(maxSteps) && maxSteps > 0 ? maxSteps : undefined,
    );
    res.json(result);
  }),
);

/** POST one RUN-NODE FORK action — the state-less runner-lane seats
 *  (the output node) forwarded to the daemon; the fork point rides the
 *  seat's own chain, the child parents under the chain root's plan row. */
router.post(
  "/threads/:threadId/nodes/:node/fork",
  wrapAsync(async (req: Request, res: Response) => {
    const goal = String((req.body as { goal?: unknown })?.goal ?? "").trim();
    const requestId = Number((req.body as { request_id?: unknown })?.request_id);
    if (goal === "") {
      throw new HttpError(400, "INVALID_PARAM", "goal is required");
    }
    if (!Number.isInteger(requestId) || requestId <= 0) {
      throw new HttpError(400, "INVALID_PARAM", "request_id must be a request id");
    }
    const maxSteps = Number((req.body as { max_steps?: unknown })?.max_steps);
    const result = await runNodeForkOnNode(
      String(req.params.threadId),
      String(req.params.node),
      goal,
      requestId,
      Number.isInteger(maxSteps) && maxSteps > 0 ? maxSteps : undefined,
    );
    res.json(result);
  }),
);

/** POST one UI FORK action — forwarded to the deep-agents daemon (the
 *  child continues the clicked agent step's recorded conversation). */
router.post(
  "/threads/:threadId/states/:stateId/fork",
  wrapAsync(async (req: Request, res: Response) => {
    const stateId = Number(req.params.stateId);
    if (!Number.isInteger(stateId)) {
      throw new HttpError(400, "INVALID_PARAM", "stateId must be an integer");
    }
    const goal = String((req.body as { goal?: unknown })?.goal ?? "").trim();
    const requestId = Number((req.body as { request_id?: unknown })?.request_id);
    if (goal === "") {
      throw new HttpError(400, "INVALID_PARAM", "goal is required");
    }
    if (!Number.isInteger(requestId) || requestId <= 0) {
      throw new HttpError(400, "INVALID_PARAM", "request_id must be a request id");
    }
    const result = await runNodeFork(
      String(req.params.threadId),
      stateId,
      goal,
      requestId,
    );
    res.json(result);
  }),
);

export default router;
