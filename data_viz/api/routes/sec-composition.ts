/**
 * Sec Composition API routes.
 */
import { Router, type Request, type Response } from "express";
import { getSecComposition } from "../services/sec-composition.service.js";

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

export default router;
