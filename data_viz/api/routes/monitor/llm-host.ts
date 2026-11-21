/**
 * Local-model-host admin — the llm area's model lifecycle surface
 * (server_cluster/llm on 8767) proxied for the Monitor area: the model
 * list with each model's GPU lifecycle state, the one-call
 * save-memory offload (every loaded model's weights GPU → CPU — an
 * operator freeing VRAM for the GPU-hungry pipelines), and the
 * per-model load/offload/unload actions.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import {
  llmHostListModels,
  llmHostModelLifecycle,
  llmHostOffloadAll,
} from "../../services/infra/llm-host-client.service.js";

const router = Router();

/** GET /api/monitor/llm-host/models — every configured local model
 *  with its lifecycle state under `local` (`loaded` = weights on the
 *  GPU, `offloaded` = in RAM off the GPU, `unloaded` = next use
 *  cold-loads from disk). */
router.get("/llm-host/models", wrapAsync(async (_req: Request, res: Response) => {
  const models = await llmHostListModels();
  res.json({ success: true, models });
}));

/** POST /api/monitor/llm-host/models/offload-all — the manual
 *  save-memory action: every LOADED model's weights move GPU → CPU
 *  right now (already-offloaded models are skipped; a failing model is
 *  skipped so the rest still offload; the next request to an offloaded
 *  model rehydrates it transparently). The answer lists what got
 *  offloaded. */
router.post("/llm-host/models/offload-all", wrapAsync(async (_req: Request, res: Response) => {
  const result = await llmHostOffloadAll();
  res.json({
    success: true,
    offloaded: result.offloaded,
    message: result.offloaded.length > 0
      ? `offloaded ${result.offloaded.length} model(s) to CPU RAM`
      : "nothing to offload — no model currently loaded on the GPU",
  });
}));

/** POST /api/monitor/llm-host/models/:id/:action — one model's admin
 *  lifecycle action (load | offload | unload). */
router.post("/llm-host/models/:id/:action", wrapAsync(async (req: Request, res: Response) => {
  const id = String(req.params.id ?? "");
  const action = String(req.params.action ?? "");
  if (!/^[A-Za-z0-9._-]+$/.test(id)) {
    throw new HttpError(400, "INVALID_PARAM", `bad model id '${id}'`);
  }
  if (action !== "load" && action !== "offload" && action !== "unload") {
    throw new HttpError(
      400,
      "INVALID_PARAM",
      `action must be load | offload | unload, got '${action}'`,
    );
  }
  const result = await llmHostModelLifecycle(id, action);
  res.json({ success: true, result });
}));

export default router;
