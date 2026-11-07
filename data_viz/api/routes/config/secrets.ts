/**
 * GET/POST /api/config/secrets, PUT/DELETE /api/config/secrets/:id, POST
 * /api/config/secrets/test — the Secrets config tab (static.secret_config,
 * the only DB-backed writable config tab).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  SECRET_TYPES,
  createSecret,
  deleteSecret,
  listSecrets,
  testSecret,
  updateSecret,
  type SecretConfigSave,
  type SecretType,
} from "../../services/secret-config.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Secrets config — static.secret_config (DB-backed, WRITABLE)
// ---------------------------------------------------------------------------

/** Validate + normalize one secret entry from the wire (throws on bad
 *  input with a user-presentable message — messages name fields, never
 *  the apiKey value). model_name: REQUIRED on llm rows (the chat
 *  payload needs it), optional on web_search rows (the engine). */
function parseSecretEntry(body: unknown): SecretConfigSave {
  const b = (typeof body === "object" && body != null ? body : {}) as Record<string, unknown>;
  const secretTypeRaw = typeof b.secretType === "string" ? b.secretType.trim() : "";
  if (!(SECRET_TYPES as readonly string[]).includes(secretTypeRaw)) {
    throw new Error(`secretType must be one of: ${SECRET_TYPES.join(" | ")}`);
  }
  const secretType = secretTypeRaw as SecretType;
  const provider = typeof b.provider === "string" ? b.provider.trim() : "";
  if (provider === "") {
    throw new Error(
      `provider must be a label of the ${secretType === "llm" ? "ext_llm_provider" : "ext_http_source"} domain`,
    );
  }
  let modelName: string | null = null;
  if (b.modelName != null) {
    if (typeof b.modelName !== "string") {
      throw new Error("modelName must be a string or null");
    }
    modelName = b.modelName.trim();
  }
  if (secretType === "llm" && (modelName == null || modelName === "")) {
    throw new Error("modelName is required for llm rows (the chat payload needs the model id)");
  }
  if (modelName === "") modelName = null;
  let contextLength: number | null = null;
  if (b.contextLength != null) {
    contextLength = Number(b.contextLength);
    if (
      !Number.isInteger(contextLength) || contextLength < 1 ||
      contextLength > 10_000_000
    ) {
      throw new Error("contextLength must be an integer in [1, 10000000] or null");
    }
  }
  let apiUrl: string | null = null;
  if (b.apiUrl != null) {
    if (typeof b.apiUrl !== "string") {
      throw new Error("apiUrl must be an http(s) URL string or null");
    }
    apiUrl = b.apiUrl.trim();
    if (apiUrl !== "" && !/^https?:\/\//i.test(apiUrl)) {
      throw new Error("apiUrl must be an http(s) URL or null");
    }
    if (apiUrl === "") apiUrl = null;
  }
  // The secret tri-state: omitted/"" = keep, null = clear, string =
  // set/replace. Anything else (a number, an object) is a client bug.
  if (b.apiKey !== undefined && b.apiKey !== null && typeof b.apiKey !== "string") {
    throw new Error("apiKey must be a string (set/replace), null (clear), or omitted (keep)");
  }
  if (b.isActive !== undefined && typeof b.isActive !== "boolean") {
    throw new Error("isActive must be a boolean");
  }
  let notes: string | null = null;
  if (b.notes != null) {
    if (typeof b.notes !== "string") throw new Error("notes must be a string or null");
    notes = b.notes;
  }
  return {
    secretType,
    provider,
    modelName,
    contextLength,
    apiUrl,
    apiKey: b.apiKey === undefined ? undefined : (b.apiKey as string | null),
    isActive: b.isActive !== false,
    notes,
  };
}

/** GET /api/config/secrets — every secret row with the key MASKED
 *  (apiKeyHint + hasApiKey; the ciphertext column is never selected). */
router.get("/secrets", async (_req: Request, res: Response) => {
  try {
    const rows = await listSecrets();
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      count: rows.length,
      rows,
    });
  } catch (err) {
    console.error("[config/secrets GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** POST /api/config/secrets — create one row (a typed apiKey string is
 *  encrypted at rest server-side; 409 when the identity triple already
 *  exists). */
router.post("/secrets", async (req: Request, res: Response) => {
  try {
    const saved = await createSecret(parseSecretEntry(req.body));
    if (saved == null) {
      res.status(409).json({
        success: false,
        error: "a secret with this type + provider + model already exists — edit it instead",
      });
      return;
    }
    res.status(201).json({ success: true, entry: saved });
  } catch (err) {
    console.error("[config/secrets POST] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

/** PUT /api/config/secrets/:id — update one row; the apiKey tri-state
 *  decides keep / clear / replace of the stored key (404 unknown id). */
router.put("/secrets/:id", async (req: Request, res: Response) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id < 1) {
    res.status(400).json({ success: false, error: "id must be an integer >= 1" });
    return;
  }
  try {
    const saved = await updateSecret(id, parseSecretEntry(req.body));
    if (saved == null) {
      res.status(404).json({ success: false, error: `no secret_config row with id ${id}` });
      return;
    }
    res.json({ success: true, entry: saved });
  } catch (err) {
    console.error("[config/secrets PUT] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

/** DELETE /api/config/secrets/:id — remove one row entirely (the row's
 *  ✕ action, behind a confirm dialog); the encrypted key goes with it
 *  (404 unknown id). */
router.delete("/secrets/:id", async (req: Request, res: Response) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id < 1) {
    res.status(400).json({ success: false, error: "id must be an integer >= 1" });
    return;
  }
  try {
    const deleted = await deleteSecret(id);
    if (!deleted) {
      res.status(404).json({ success: false, error: `no secret_config row with id ${id}` });
      return;
    }
    res.json({ success: true });
  } catch (err) {
    console.error("[config/secrets DELETE] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

/** POST /api/config/secrets/test — one real minimal request per type
 *  (llm: chat-completions ping; web_search: the provider's own 1-hit
 *  search) against the CURRENT config (the dialog's form values; the
 *  stored row fills in omitted key/url). The key rides the request once
 *  and is never stored, logged, or echoed — the result carries status,
 *  latency and a reply snippet only. */
router.post("/secrets/test", async (req: Request, res: Response) => {
  const b = (typeof req.body === "object" && req.body != null ? req.body : {}) as Record<string, unknown>;
  const secretTypeRaw = typeof b.secretType === "string" ? b.secretType.trim() : "";
  if (!(SECRET_TYPES as readonly string[]).includes(secretTypeRaw)) {
    res.status(400).json({ success: false, error: `secretType must be one of: ${SECRET_TYPES.join(" | ")}` });
    return;
  }
  const secretType = secretTypeRaw as SecretType;
  const provider = typeof b.provider === "string" ? b.provider.trim() : "";
  if (provider === "") {
    res.status(400).json({ success: false, error: "provider must be a label of the provider domain selected by secretType" });
    return;
  }
  const modelName = b.modelName === null
    ? null
    : (typeof b.modelName === "string" ? b.modelName.trim() : null);
  let secretId: number | undefined;
  if (b.secretId != null) {
    secretId = Number(b.secretId);
    if (!Number.isInteger(secretId) || secretId < 1) {
      res.status(400).json({ success: false, error: "secretId must be an integer >= 1 or null" });
      return;
    }
  }
  let apiUrl: string | null = null;
  if (b.apiUrl != null) {
    if (typeof b.apiUrl !== "string") {
      res.status(400).json({ success: false, error: "apiUrl must be a string or null" });
      return;
    }
    apiUrl = b.apiUrl.trim();
  }
  if (b.apiKey !== undefined && b.apiKey !== null && typeof b.apiKey !== "string") {
    res.status(400).json({ success: false, error: "apiKey must be a string, null, or omitted" });
    return;
  }
  try {
    const result = await testSecret({
      secretId,
      secretType,
      provider,
      modelName,
      apiUrl,
      apiKey: b.apiKey === undefined ? undefined : (b.apiKey as string | null),
    });
    res.json({ success: true, result });
  } catch (err) {
    // err messages name fields, never the apiKey value.
    console.error("[config/secrets/test POST] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

export default router;
