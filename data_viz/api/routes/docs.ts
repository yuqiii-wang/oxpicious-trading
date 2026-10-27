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
import { listDocs } from "../services/docs.service.js";

const router = Router();

router.get("/", async (_req, res) => {
  try {
    res.json(await listDocs());
  } catch (err) {
    console.error("[docs] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
