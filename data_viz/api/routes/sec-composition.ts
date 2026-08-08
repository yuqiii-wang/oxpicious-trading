/**
 * Sec Composition API routes.
 */
import { Router, type Request, type Response } from "express";
import { getSecComposition, getLinkedEtfs, getSimilarIndices } from "../services/sec-composition.service.js";

const router = Router();

/** GET /api/sec-composition?code=159001.SZ */
router.get("/", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getSecComposition(code));
  } catch (err) {
    console.error("[sec-composition] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/sec-composition/linked-etfs?code=000300
 *  Returns the ETFs tracking the given index (parent_index_code = code),
 *  enriched with latest close + n_days from stats.v_etf_margin. */
router.get("/linked-etfs", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getLinkedEtfs(code));
  } catch (err) {
    console.error("[sec-composition/linked-etfs] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/sec-composition/similar-indices?code=000300
 *  Returns the top-3 similar indices by mutual shared composition weight for
 *  the given index, from stats.sec_similars (sec_type='index', latest snapshot <= today). */
router.get("/similar-indices", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getSimilarIndices(code));
  } catch (err) {
    console.error("[sec-composition/similar-indices] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
