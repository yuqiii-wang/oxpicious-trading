/**
 * GET/PUT /api/config/sec-statics(/file) — the Security Classification
 * tab (database/sec_statics/ tree).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError } from "../../lib/http.js";
import {
  listSecStaticsFiles,
  readSecStaticsFile,
  writeSecStaticsFile,
  type SecStaticsFile,
} from "../../services/sec/sec-statics.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Security classification statics — database/sec_statics/ tree
// ---------------------------------------------------------------------------

/** GET /api/config/sec-statics — the file inventory (grouping areas,
 *  generated flags, sizes, mtimes). Content is fetched per file on
 *  selection via /sec-statics/file. */
router.get("/sec-statics", (_req: Request, res: Response) => {
  const files: SecStaticsFile[] = listSecStaticsFiles();
  res.json({
    success: true,
    generatedAt: new Date().toISOString(),
    files,
  });
});

/** GET /api/config/sec-statics/file?path=… — one file's raw text.
 *  The service throws plain Errors for every bad path (unusable path,
 *  non-JSON path, not in the tree); the route's historical mapping is
 *  404 for all of them, preserved by the rethrow below. */
router.get("/sec-statics/file", (req: Request, res: Response) => {
  const p = typeof req.query.path === "string" ? req.query.path : "";
  try {
    const { file, text } = readSecStaticsFile(p);
    res.json({ success: true, file, text });
  } catch (err) {
    // The service only ever throws Error instances.
    throw HttpError.notFound((err as Error).message);
  }
});

/** PUT /api/config/sec-statics/file — write one file's text verbatim
 *  (JSON-parse validated server-side; 400 with the parse error when
 *  invalid). The service throws plain Errors (bad path, missing file,
 *  invalid JSON, post-write IO) — the route's historical mapping is 400
 *  for all of them, preserved by the rethrow below. */
router.put("/sec-statics/file", (req: Request, res: Response) => {
  const b = (typeof req.body === "object" && req.body != null ? req.body : {}) as Record<string, unknown>;
  const p = typeof b.path === "string" ? b.path : "";
  const text = typeof b.text === "string" ? b.text : null;
  if (text == null) {
    throw new HttpError(400, "INVALID_PARAM", "body must be { path, text }");
  }
  try {
    const file = writeSecStaticsFile(p, text);
    res.json({ success: true, file });
  } catch (err) {
    // The service only ever throws Error instances.
    throw new HttpError(400, "INVALID_PARAM", (err as Error).message);
  }
});

export default router;
