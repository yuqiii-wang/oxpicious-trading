/**
 * GET/POST /api/config/secrets, PUT/DELETE /api/config/secrets/:id, POST
 * /api/config/secrets/test — the Secrets config tab (static.secret_config,
 * the only DB-backed writable config tab).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import {
  LLM_MODALITIES,
  SECRET_SUB_TYPES,
  SECRET_TYPES,
  createSecret,
  deleteSecret,
  listSecrets,
  testSecret,
  updateSecret,
  type LlmModality,
  type SecretConfigSave,
  type SecretSubType,
  type SecretType,
} from "../../services/infra/secret-config.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Secrets config — static.secret_config (DB-backed, WRITABLE)
// ---------------------------------------------------------------------------

/** Validate + normalize one secret entry from the wire (throws
 *  HttpError 400 INVALID_PARAM with a user-presentable message — messages
 *  name fields, never the apiKey value). model_name: REQUIRED on llm
 *  and jev rows (the chat payload / the systemone `model` routing need
 *  the model id), optional on web_search rows (the engine) and
 *  tokenization rows (the endpoint resolves the host's single
 *  registered segmenter). */
function parseSecretEntry(body: unknown): SecretConfigSave {
  const b = (typeof body === "object" && body != null ? body : {}) as Record<string, unknown>;
  const secretTypeRaw = typeof b.secretType === "string" ? b.secretType.trim() : "";
  if (!(SECRET_TYPES as readonly string[]).includes(secretTypeRaw)) {
    throw new HttpError(400, "INVALID_PARAM",`secretType must be one of: ${SECRET_TYPES.join(" | ")}`);
  }
  const secretType = secretTypeRaw as SecretType;
  const provider = typeof b.provider === "string" ? b.provider.trim() : "";
  if (provider === "") {
    throw new HttpError(400, "INVALID_PARAM",
      `provider must be a label of the ${secretType === "web_search" ? "ext_http_source" : "ext_llm_provider"} domain`,
    );
  }
  // The service tier ('secret_sub_type') — omitted = general (the
  // column's own default; the UI always sends it explicitly).
  let subType: SecretSubType = "general";
  if (b.subType !== undefined) {
    if (!(SECRET_SUB_TYPES as readonly string[]).includes(b.subType as string)) {
      throw new HttpError(400, "INVALID_PARAM",
        `subType must be one of: ${SECRET_SUB_TYPES.join(" | ")}`);
    }
    subType = b.subType as SecretSubType;
  }
  let modelName: string | null = null;
  if (b.modelName != null) {
    if (typeof b.modelName !== "string") {
      throw new HttpError(400, "INVALID_PARAM","modelName must be a string or null");
    }
    modelName = b.modelName.trim();
  }
  if ((secretType === "llm" || secretType === "jev" ||
       secretType === "ocr") &&
      (modelName == null || modelName === "")) {
    throw new HttpError(400, "INVALID_PARAM",
      `modelName is required for ${secretType} rows (the request payload needs the model id)`);
  }
  if (modelName === "") modelName = null;
  // The modality profiles ('llm_modality') — omitted = text (the
  // columns' own default; the UI always sends them explicitly).
  let inputModality: LlmModality = "text";
  let outputModality: LlmModality = "text";
  for (const [field, value] of [["inputModality", b.inputModality], ["outputModality", b.outputModality]] as const) {
    if (value !== undefined) {
      if (!(LLM_MODALITIES as readonly string[]).includes(value as string)) {
        throw new HttpError(400, "INVALID_PARAM",
          `${field} must be one of: ${LLM_MODALITIES.join(" | ")}`);
      }
      if (field === "inputModality") inputModality = value as LlmModality;
      else outputModality = value as LlmModality;
    }
  }
  let contextLength: number | null = null;
  if (b.contextLength != null) {
    contextLength = Number(b.contextLength);
    if (
      !Number.isInteger(contextLength) || contextLength < 1 ||
      contextLength > 10_000_000
    ) {
      throw new HttpError(400, "INVALID_PARAM","contextLength must be an integer in [1, 10000000] or null");
    }
  }
  let apiUrl: string | null = null;
  if (b.apiUrl != null) {
    if (typeof b.apiUrl !== "string") {
      throw new HttpError(400, "INVALID_PARAM","apiUrl must be an http(s) URL string or null");
    }
    apiUrl = b.apiUrl.trim();
    if (apiUrl !== "" && !/^https?:\/\//i.test(apiUrl)) {
      throw new HttpError(400, "INVALID_PARAM","apiUrl must be an http(s) URL or null");
    }
    if (apiUrl === "") apiUrl = null;
  }
  // The secret tri-state: omitted/"" = keep, null = clear, string =
  // set/replace. Anything else (a number, an object) is a client bug.
  if (b.apiKey !== undefined && b.apiKey !== null && typeof b.apiKey !== "string") {
    throw new HttpError(400, "INVALID_PARAM","apiKey must be a string (set/replace), null (clear), or omitted (keep)");
  }
  if (b.isActive !== undefined && typeof b.isActive !== "boolean") {
    throw new HttpError(400, "INVALID_PARAM","isActive must be a boolean");
  }
  let notes: string | null = null;
  if (b.notes != null) {
    if (typeof b.notes !== "string") throw new HttpError(400, "INVALID_PARAM","notes must be a string or null");
    notes = b.notes;
  }
  return {
    secretType,
    provider,
    subType,
    modelName,
    contextLength,
    inputModality,
    outputModality,
    apiUrl,
    apiKey: b.apiKey === undefined ? undefined : (b.apiKey as string | null),
    isActive: b.isActive !== false,
    notes,
  };
}

/** GET /api/config/secrets — every secret row with the key MASKED
 *  (apiKeyHint + hasApiKey; the ciphertext column is never selected). */
router.get("/secrets", wrapAsync(async (_req: Request, res: Response) => {
  const rows = await listSecrets();
  res.json({
    success: true,
    generatedAt: new Date().toISOString(),
    count: rows.length,
    rows,
  });
}));

/** POST /api/config/secrets — create one row (a typed apiKey string is
 *  encrypted at rest server-side; 409 when the identity triple already
 *  exists). */
router.post("/secrets", wrapAsync(async (req: Request, res: Response) => {
  const saved = await createSecret(parseSecretEntry(req.body));
  if (saved == null) {
    throw HttpError.conflict(
      "a secret with this type + provider + model already exists — edit it instead",
    );
  }
  res.status(201).json({ success: true, entry: saved });
}));

/** PUT /api/config/secrets/:id — update one row; the apiKey tri-state
 *  decides keep / clear / replace of the stored key (404 unknown id). */
router.put("/secrets/:id", wrapAsync(async (req: Request, res: Response) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id < 1) {
    throw new HttpError(400, "INVALID_PARAM", "id must be an integer >= 1");
  }
  const saved = await updateSecret(id, parseSecretEntry(req.body));
  if (saved == null) {
    throw HttpError.notFound(`no secret_config row with id ${id}`);
  }
  res.json({ success: true, entry: saved });
}));

/** DELETE /api/config/secrets/:id — remove one row entirely (the row's
 *  ✕ action, behind a confirm dialog); the encrypted key goes with it
 *  (404 unknown id). */
router.delete("/secrets/:id", wrapAsync(async (req: Request, res: Response) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id < 1) {
    throw new HttpError(400, "INVALID_PARAM", "id must be an integer >= 1");
  }
  const deleted = await deleteSecret(id);
  if (!deleted) {
    throw HttpError.notFound(`no secret_config row with id ${id}`);
  }
  res.json({ success: true });
}));

/** POST /api/config/secrets/test — one real minimal request per type
 *  (llm: chat-completions ping; web_search: the provider's own 1-hit
 *  search) against the CURRENT config (the dialog's form values; the
 *  stored row fills in omitted key/url). The key rides the request once
 *  and is never stored, logged, or echoed — the result carries status,
 *  latency and a reply snippet only. */
router.post("/secrets/test", wrapAsync(async (req: Request, res: Response) => {
  const b = (typeof req.body === "object" && req.body != null ? req.body : {}) as Record<string, unknown>;
  const secretTypeRaw = typeof b.secretType === "string" ? b.secretType.trim() : "";
  if (!(SECRET_TYPES as readonly string[]).includes(secretTypeRaw)) {
    throw new HttpError(400, "INVALID_PARAM", `secretType must be one of: ${SECRET_TYPES.join(" | ")}`);
  }
  const secretType = secretTypeRaw as SecretType;
  const provider = typeof b.provider === "string" ? b.provider.trim() : "";
  if (provider === "") {
    throw new HttpError(400, "INVALID_PARAM", "provider must be a label of the provider domain selected by secretType");
  }
  const modelName = b.modelName === null
    ? null
    : (typeof b.modelName === "string" ? b.modelName.trim() : null);
  let secretId: number | undefined;
  if (b.secretId != null) {
    secretId = Number(b.secretId);
    if (!Number.isInteger(secretId) || secretId < 1) {
      throw new HttpError(400, "INVALID_PARAM", "secretId must be an integer >= 1 or null");
    }
  }
  let apiUrl: string | null = null;
  if (b.apiUrl != null) {
    if (typeof b.apiUrl !== "string") {
      throw new HttpError(400, "INVALID_PARAM", "apiUrl must be a string or null");
    }
    apiUrl = b.apiUrl.trim();
  }
  if (b.apiKey !== undefined && b.apiKey !== null && typeof b.apiKey !== "string") {
    throw new HttpError(400, "INVALID_PARAM", "apiKey must be a string, null, or omitted");
  }
  // testSecret's thrown messages name fields, never the apiKey value.
  const result = await testSecret({
    secretId,
    secretType,
    provider,
    modelName,
    apiUrl,
    apiKey: b.apiKey === undefined ? undefined : (b.apiKey as string | null),
  });
  res.json({ success: true, result });
}));

export default router;
