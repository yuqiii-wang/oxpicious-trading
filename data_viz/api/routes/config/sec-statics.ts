/**
 * GET/PUT /api/config/sec-statics(/file) — the Security Classification
 * tab (database/sec_statics/ tree).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listSecStaticsFiles,
  readSecStaticsFile,
  writeSecStaticsFile,
  type SecStaticsFile,
} from "../../services/sec-statics.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Security classification statics — database/sec_statics/ tree
// ---------------------------------------------------------------------------

/** GET /api/config/sec-statics — the file inventory (grouping areas,
 *  generated flags, sizes, mtimes). Content is fetched per file on
 *  selection via /sec-statics/file. */
router.get("/sec-statics", (_req: Request, res: Response) => {
  try {
    const files: SecStaticsFile[] = listSecStaticsFiles();
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      files,
    });
  } catch (err) {
    console.error("[config/sec-statics GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** GET /api/config/sec-statics/file?path=… — one file's raw text. */
router.get("/sec-statics/file", (req: Request, res: Response) => {
  const p = typeof req.query.path === "string" ? req.query.path : "";
  try {
    const { file, text } = readSecStaticsFile(p);
    res.json({ success: true, file, text });
  } catch (err) {
    console.error("[config/sec-statics/file GET] error:", err);
    res.status(404).json({ success: false, error: String(err) });
  }
});

/** PUT /api/config/sec-statics/file — write one file's text verbatim
 *  (JSON-parse validated server-side; 400 with the parse error when
 *  invalid). */
router.put("/sec-statics/file", (req: Request, res: Response) => {
  const b = (typeof req.body === "object" && req.body != null ? req.body : {}) as Record<string, unknown>;
  const p = typeof b.path === "string" ? b.path : "";
  const text = typeof b.text === "string" ? b.text : null;
  if (text == null) {
    res.status(400).json({ success: false, error: "body must be { path, text }" });
    return;
  }
  try {
    const file = writeSecStaticsFile(p, text);
    res.json({ success: true, file });
  } catch (err) {
    console.error("[config/sec-statics/file PUT] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

export default router;
