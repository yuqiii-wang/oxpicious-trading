/**
 * docs routes — the static.docs UI-description registry.
 *
 *   GET /api/docs
 *       The whole registry (static.docs, INSERT-seeded by
 *       database/sql/static/docs/static/): one row per described UI
 *       element — chart AI-Ask spec, info popover, column info — as
 *       {{param}} templates the client resolves.
 */
import { Router } from "express";
import { wrapAsync } from "../lib/http.js";
import { listDocs } from "../services/knowledge/docs.service.js";

const router = Router();

router.get("/", wrapAsync(async (_req, res) => {
  res.json(await listDocs());
}));

export default router;
