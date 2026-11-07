/**
 * Secrets config service — CRUD + connection test over
 * static.secret_config for the Config page's Secrets tab (DDL:
 * database/sql/static/secrets/, write actions:
 * database/sql_writes/data_viz/api/secret_config/{insert,update}.sql —
 * THIS service supplies the parameters and owns the SECRET HANDLING,
 * the action files own the SQL).
 *
 * The registry is GENERIC in credential type (secret_type SMALLINT
 * label code: 0 llm | 1 web_search) and the provider column follows
 * the audit's SELECTED-DOMAIN contract: decoded via 'ext_llm_provider'
 * for llm rows, 'ext_http_source' for web_search rows.
 *
 * The masking contract (see also secret-box.service):
 *   - NO query here ever selects api_key_enc for a RETURN — the
 *     ciphertext never leaves the DB (the test reads it internally).
 *   - A read returns api_key_hint (masked preview) + hasApiKey.
 *   - A write takes the wire's OPTIONAL plaintext apiKey: undefined ""
 *     = keep the stored key, null = clear it, a string = encrypt and
 *     replace. The plaintext exists only inside this call — it is not
 *     logged (no error message embeds parameter values) and only the
 *     encrypted blob + hint land in the row.
 *
 * The INSERT/UPDATE statements route to the primary pool (writes are
 * outside db.service's read whitelist). listSecrets is a plain read
 * (replica routing + failover per db.service).
 */
import fs from "fs";
import path from "path";
import crypto from "crypto";
import type { QueryResultRow } from "pg";
import { queryRows } from "../lib/db.js";
import { querySqlView } from "../lib/sql-views.js";
import { decodeLabel, encodeLabelParam } from "./label-codes.js";
import { decryptSecret, encryptSecret, secretHint } from "./secret-box.service.js";
import { loadSqlWrite } from "./sql-writes.service.js";

/** The closed secret_type vocabulary (mirror of
 *  static.label_dim 'secret_type'). */
export const SECRET_TYPES = ["llm", "web_search"] as const;
export type SecretType = (typeof SECRET_TYPES)[number];

/** The provider domain SELECTED BY secret_type (the audit pattern). */
function providerDomain(secretType: SecretType): "ext_llm_provider" | "ext_http_source" {
  return secretType === "llm" ? "ext_llm_provider" : "ext_http_source";
}

/** One static.secret_config row as the write actions RETURN it (never
 *  api_key_enc). */
export interface SecretConfigRow extends QueryResultRow {
  secret_id: number;
  secret_type: number;
  provider: number;
  model_name: string | null;
  context_length: number | null;
  api_url: string | null;
  api_key_hint: string | null;
  is_active: boolean;
  notes: string | null;
  updated_at: Date | string;
}

/** The wire shape (camelCase): everything EXCEPT any key material —
 *  apiKeyHint is the masked preview, hasApiKey its boolean summary. */
export interface SecretConfigPayload {
  secretId: number;
  /** Decoded secret_type label (llm | web_search). */
  secretType: SecretType;
  secretTypeCode: number;
  /** Decoded provider label (domain selected by secretType). */
  provider: string;
  providerCode: number;
  modelName: string | null;
  contextLength: number | null;
  apiUrl: string | null;
  apiKeyHint: string | null;
  hasApiKey: boolean;
  isActive: boolean;
  notes: string | null;
  updatedAt: string;
}

/** A create/update body from the UI (already server-validated in
 *  routes/config/secrets.ts). `apiKey` is the tri-state secret field:
 *  omitted/"" = keep, null = clear, string = set/replace. */
export interface SecretConfigSave {
  secretType: SecretType;
  provider: string;
  modelName: string | null;
  contextLength: number | null;
  apiUrl: string | null;
  apiKey?: string | null;
  isActive: boolean;
  notes: string | null;
}

function toSecretConfigPayload(r: SecretConfigRow): SecretConfigPayload {
  const secretType = (SECRET_TYPES as readonly string[])[r.secret_type] ?? String(r.secret_type);
  return {
    secretId: r.secret_id,
    secretType: secretType as SecretType,
    secretTypeCode: r.secret_type,
    provider: decodeLabel(providerDomain(secretType as SecretType), r.provider)
      ?? String(r.provider),
    providerCode: r.provider,
    modelName: r.model_name,
    contextLength: r.context_length,
    apiUrl: r.api_url,
    apiKeyHint: r.api_key_hint,
    hasApiKey: r.api_key_hint != null,
    isActive: r.is_active,
    notes: r.notes,
    updatedAt:
      r.updated_at instanceof Date
        ? r.updated_at.toISOString()
        : String(r.updated_at ?? ""),
  };
}

/** Every active+retired secret row, type/provider/model order. */
export async function listSecrets(): Promise<SecretConfigPayload[]> {
  // The SELECT lives in the sql view
  // data_viz/api/services/secret-config/list.
  const rows = await querySqlView<SecretConfigRow>(
    "data_viz/api/services/secret-config/list",
  );
  return rows.map(toSecretConfigPayload);
}

/** Encode + mask the wire's plaintext (null pair when absent). */
function buildKeyPair(apiKey: unknown): { enc: string | null; hint: string | null } {
  const key = typeof apiKey === "string" ? apiKey.trim() : "";
  if (key === "") return { enc: null, hint: null };
  return { enc: encryptSecret(key), hint: secretHint(key) };
}

/** Create one secret row. Returns the saved row, or null when the
 *  (type, provider, model) triple already exists — the caller maps
 *  null to 409 ("edit it instead"). */
export async function createSecret(
  input: SecretConfigSave,
): Promise<SecretConfigPayload | null> {
  const provider = encodeLabelParam(providerDomain(input.secretType), input.provider);
  if (provider == null) {
    throw new Error(
      `provider '${input.provider}' is not a known ${providerDomain(input.secretType)} label`,
    );
  }
  const { enc, hint } = buildKeyPair(input.apiKey);
  const rows = await queryRows<SecretConfigRow>(
    loadSqlWrite("data_viz", "api", "secret_config", "insert"),
    [
      encodeLabelParam("secret_type", input.secretType),
      provider,
      input.modelName,
      input.contextLength,
      input.apiUrl,
      enc,
      hint,
      input.isActive,
      input.notes,
    ],
  );
  return rows.length > 0 ? toSecretConfigPayload(rows[0]) : null;
}

/** Update one secret row by id. Key handling per the tri-state: keep
 *  (apiKey omitted/""), clear (null), replace (string). Returns the
 *  refreshed row, or null when no row carries the id (404 upstream). */
export async function updateSecret(
  secretId: number,
  input: SecretConfigSave,
): Promise<SecretConfigPayload | null> {
  const provider = encodeLabelParam(providerDomain(input.secretType), input.provider);
  if (provider == null) {
    throw new Error(
      `provider '${input.provider}' is not a known ${providerDomain(input.secretType)} label`,
    );
  }
  let keyMode: 0 | 1 | 2 = 0;
  let enc: string | null = null;
  let hint: string | null = null;
  if (input.apiKey === null) {
    keyMode = 2;
  } else if (typeof input.apiKey === "string" && input.apiKey.trim() !== "") {
    keyMode = 1;
    ({ enc, hint } = buildKeyPair(input.apiKey));
  }
  const rows = await queryRows<SecretConfigRow>(
    loadSqlWrite("data_viz", "api", "secret_config", "update"),
    [
      secretId,
      encodeLabelParam("secret_type", input.secretType),
      provider,
      input.modelName,
      input.contextLength,
      input.apiUrl,
      keyMode,
      enc,
      hint,
      input.isActive,
      input.notes,
    ],
  );
  return rows.length > 0 ? toSecretConfigPayload(rows[0]) : null;
}

/** Delete one secret row entirely (the row's ✕ action, behind a
 *  confirm dialog) — the encrypted key goes WITH it. Returns false
 *  when no row carries the id (404 upstream). */
export async function deleteSecret(secretId: number): Promise<boolean> {
  const rows = await queryRows<{ secret_id: number }>(
    loadSqlWrite("data_viz", "api", "secret_config", "delete"),
    [secretId],
  );
  return rows.length > 0;
}

// ---------------------------------------------------------------------------
//  Connection test
// ---------------------------------------------------------------------------

/** A row INCLUDING the ciphertext — the test's internal shape, never
 *  returned to a client. */
interface SecretConfigRowWithKey extends SecretConfigRow {
  api_key_enc: string | null;
}

/** Default endpoint per (secret_type, provider) — the mirror of the
 *  Python providers' class constants (ZhipuChatProvider.CHAT_URL,
 *  ZhipuSearchProvider.SEARCH_URL, zhihu.py's ZHIHU_SEARCH_URL). A
 *  combination without an entry here must carry an explicit api_url to
 *  be testable. */
const PROVIDER_DEFAULT_URLS: Record<string, string> = {
  "llm/zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
  "web_search/zhipu": "https://open.bigmodel.cn/api/paas/v4/web_search",
  "web_search/zhihu": "https://developer.zhihu.com/api/v1/content/zhihu_search",
};

/** The env fallback chain, mirroring BaseLlmProvider.API_KEY_ENV_VARS:
 *  explicit env wins, then the ROOT .env (the file the Python providers
 *  read; database/.env holds no LLM keys — the project's keys migrated
 *  into static.secret_config 2026-11-06 and the root .env was deleted,
 *  so this path stays for externally-set environments only). Returns
 *  the first non-empty. */
function envFallbackKey(): string | null {
  const names = ["GLM_ONLINE_SEARCH_KEY", "ZHIPU_API_KEY", "BIGMODEL_API_KEY"];
  for (const name of names) {
    const v = process.env[name]?.trim();
    if (v) return v;
  }
  const envPath = path.resolve(
    import.meta.dirname ?? __dirname,
    "..",
    "..",
    "..",
    ".env",
  );
  try {
    const lines = fs.readFileSync(envPath, "utf-8").split(/\r?\n/);
    for (const name of names) {
      for (const raw of lines) {
        const line = raw.trim();
        if (!line || line.startsWith("#") || !line.includes("=")) continue;
        const [k, ...rest] = line.split("=");
        if (k.trim() === name) {
          const v = rest.join("=").trim();
          if (v) return v;
        }
      }
    }
  } catch {
    // no root .env — fall through
  }
  return null;
}

export interface SecretTestInput {
  /** Test the STORED row (its key/url fill in what the form omits). */
  secretId?: number;
  secretType: SecretType;
  provider: string;
  modelName: string | null;
  apiUrl?: string | null;
  /** Plaintext typed in the form — used for THIS test only, never
   *  stored. Omitted = stored key, else env fallback. */
  apiKey?: string | null;
}

export interface SecretTestResult {
  ok: boolean;
  statusCode: number | null;
  latencyMs: number | null;
  /** The endpoint actually tested (no query string, not secret). */
  endpoint: string | null;
  /** Where the key came from — form (typed), stored (row, decrypted),
   *  env (the .env fallback), none. */
  keySource: "form" | "stored" | "env" | "none";
  model: string | null;
  /** First bytes of the reply/first hit title, when the endpoint
   *  answered. */
  replySnippet: string | null;
  error: string | null;
}

function testFailure(
  partial: Partial<SecretTestResult>,
): SecretTestResult {
  return {
    ok: false,
    statusCode: null,
    latencyMs: null,
    endpoint: null,
    keySource: "none",
    model: null,
    replySnippet: null,
    error: null,
    ...partial,
  };
}

/** One REAL minimal request against the CURRENT config (the form's
 *  values, with the stored row / env fallback filling the gaps) — a
 *  chat-completions ping for llm rows, a 1-hit search for web_search
 *  rows (each provider's own protocol). The key exists only inside
 *  this call — the result carries status, latency and a snippet,
 *  never key material. */
export async function testSecret(
  input: SecretTestInput,
): Promise<SecretTestResult> {
  const provider = encodeLabelParam(providerDomain(input.secretType), input.provider);
  if (provider == null) {
    throw new Error(
      `provider '${input.provider}' is not a known ${providerDomain(input.secretType)} label`,
    );
  }
  const modelName = input.modelName?.trim() || null;

  // The stored row (by id, or by the form's type+provider+model triple)
  // fills in whatever the form omits — key and/or endpoint.
  let stored: SecretConfigRowWithKey | null = null;
  if (input.secretId != null) {
    // The SELECTs live in the sql views
    // data_viz/api/services/secret-config/by_id and .../by_triple.
    const rows = await querySqlView<SecretConfigRowWithKey>(
      "data_viz/api/services/secret-config/by_id",
      { secret_id: input.secretId },
    );
    stored = rows[0] ?? null;
  } else {
    const rows = await querySqlView<SecretConfigRowWithKey>(
      "data_viz/api/services/secret-config/by_triple",
      {
        secret_type: encodeLabelParam("secret_type", input.secretType),
        provider,
        model_name: modelName,
      },
    );
    stored = rows[0] ?? null;
  }

  const typeKey = `${input.secretType}/${input.provider.trim()}`;
  const url =
    (input.apiUrl?.trim() ||
      stored?.api_url?.trim() ||
      PROVIDER_DEFAULT_URLS[typeKey]) ?? null;
  if (!url) {
    return testFailure({
      model: modelName,
      error: `no endpoint to test — ${typeKey} has no default URL and no api_url is set`,
    });
  }

  // Key resolution: typed plaintext > stored ciphertext > env fallback.
  const typed = input.apiKey?.trim() ?? "";
  let key: string | null = null;
  let keySource: SecretTestResult["keySource"] = "none";
  if (typed !== "") {
    key = typed;
    keySource = "form";
  } else if (stored?.api_key_enc) {
    try {
      key = decryptSecret(stored.api_key_enc);
      keySource = "stored";
    } catch (err) {
      return testFailure({
        model: modelName,
        endpoint: url,
        error: `stored API key undecryptable (${String(err).replace(/^Error: /, "")}) — type the key to test it directly`,
      });
    }
  } else {
    const envKey = envFallbackKey();
    if (envKey) {
      key = envKey;
      keySource = "env";
    }
  }
  if (!key) {
    return testFailure({
      model: modelName,
      endpoint: url,
      error: "no API key available — type one or store one on the row",
    });
  }

  if (input.secretType === "web_search") {
    return input.provider.trim() === "zhihu"
      ? testZhihuSearch(url, key, keySource, modelName)
      : testZhipuWebSearch(url, key, keySource, modelName);
  }
  return testLlmChat(url, key, keySource, modelName ?? "");
}

async function testLlmChat(
  url: string, key: string, keySource: SecretTestResult["keySource"],
  model: string,
): Promise<SecretTestResult> {
  if (model === "") {
    return testFailure({
      endpoint: url, keySource,
      error: "llm rows need a model name to test (the chat payload requires it)",
    });
  }
  const started = Date.now();
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        model,
        messages: [
          { role: "user", content: "Connection test — reply with the single word: pong" },
        ],
        max_tokens: 512,
        stream: false,
        request_id: crypto.randomUUID(),
      }),
      signal: AbortSignal.timeout(15_000),
    });
    const latencyMs = Date.now() - started;
    const body = (await res.json().catch(() => null)) as
      | { choices?: Array<{ message?: { content?: string | null } }>; error?: { message?: string } | string; message?: string }
      | null;
    return judged(res, res.ok && (body?.choices?.length ?? 0) > 0, {
      latencyMs, url, keySource, model, body,
      snippet: body?.choices?.[0]?.message?.content ?? null,
    });
  } catch (err) {
    return requestFailed(err, { url, keySource, model, started });
  }
}

/** ZhiPu web_search — POST {search_query, search_engine, count:1}. */
async function testZhipuWebSearch(
  url: string, key: string, keySource: SecretTestResult["keySource"],
  engine: string | null,
): Promise<SecretTestResult> {
  const started = Date.now();
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        search_query: "connection test",
        search_engine: engine || "search_pro",
        count: 1,
        request_id: crypto.randomUUID(),
      }),
      signal: AbortSignal.timeout(15_000),
    });
    const latencyMs = Date.now() - started;
    const body = (await res.json().catch(() => null)) as
      | { search_result?: Array<{ title?: string }>; error?: { message?: string } | string; message?: string }
      | null;
    const hits = body?.search_result ?? [];
    return judged(res, res.ok && hits.length > 0, {
      latencyMs, url, keySource, model: engine, body,
      snippet: hits[0]?.title ?? null,
    });
  } catch (err) {
    return requestFailed(err, { url, keySource, model: engine, started });
  }
}

/** Zhihu content search — GET ?Query=…&Count=1 with Bearer +
 *  X-Request-Timestamp (the provider's own contract,
 *  online_search_summary/providers/zhihu.py). */
async function testZhihuSearch(
  url: string, key: string, keySource: SecretTestResult["keySource"],
  _engine: string | null,
): Promise<SecretTestResult> {
  const started = Date.now();
  const target = `${url}${url.includes("?") ? "&" : "?"}Query=${encodeURIComponent("connection test")}&Count=1`;
  try {
    const res = await fetch(target, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${key}`,
        "X-Request-Timestamp": String(Math.floor(Date.now() / 1000)),
      },
      signal: AbortSignal.timeout(15_000),
    });
    const latencyMs = Date.now() - started;
    const body = (await res.json().catch(() => null)) as
      | { Code?: number; Message?: string; Data?: { Items?: Array<{ Title?: string }> } }
      | null;
    const ok = res.ok && body?.Code === 0 && (body?.Data?.Items?.length ?? 0) > 0;
    let error: string | null = null;
    if (!res.ok) {
      error = `HTTP ${res.status}`;
    } else if (body?.Code !== 0) {
      error = `API Code ${body?.Code}: ${body?.Message ?? "no message"}`;
    } else if (!ok) {
      error = "Code 0 but no Items returned";
    }
    return {
      ok,
      statusCode: res.status,
      latencyMs,
      endpoint: url,
      keySource,
      model: _engine,
      replySnippet: body?.Data?.Items?.[0]?.Title?.slice(0, 80) ?? null,
      error: ok ? null : (error ?? "test failed").slice(0, 300),
    };
  } catch (err) {
    return requestFailed(err, { url, keySource, model: _engine, started });
  }
}

/** Shared verdict shaping for the fetch-based probes: an error body's
 *  message text (never the key) becomes the failure detail. */
function judged(
  res: Response,
  ok: boolean,
  args: {
    latencyMs: number; url: string;
    keySource: SecretTestResult["keySource"];
    model: string | null;
    body: unknown;
    snippet: string | null;
  },
): SecretTestResult {
  let error: string | null = null;
  if (!ok) {
    const body = args.body as { error?: { message?: string } | string; message?: string } | null;
    const detail =
      (typeof body?.error === "object" ? body.error.message : body?.error) ??
      body?.message ??
      `HTTP ${res.status}`;
    error = String(detail).slice(0, 300);
  }
  return {
    ok,
    statusCode: res.status,
    latencyMs: args.latencyMs,
    endpoint: args.url,
    keySource: args.keySource,
    model: args.model,
    replySnippet: ok ? args.snippet : null,
    error,
  };
}

function requestFailed(
  err: unknown,
  args: {
    url: string; keySource: SecretTestResult["keySource"];
    model: string | null; started: number;
  },
): SecretTestResult {
  return testFailure({
    model: args.model,
    endpoint: args.url,
    keySource: args.keySource,
    latencyMs: Date.now() - args.started,
    error: `request failed: ${String(err).replace(/^Error: /, "")}`.slice(0, 300),
  });
}
