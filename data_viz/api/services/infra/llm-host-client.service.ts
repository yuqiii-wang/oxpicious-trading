/**
 * Local-model-host client — THE typed HTTP client for the server
 * cluster's llm area (server_cluster/llm, FastAPI on 127.0.0.1:8767),
 * mirroring monitor-client.service.ts: one fetch with an abort timeout,
 * Error on ANY failure (network, non-OK status, timeout) so the routes
 * surface the message. Covers only the host's ADMIN face (model list +
 * lifecycle); the chat/jev/tokenization request surfaces stay with
 * their in-repo Python clients.
 */

const LLM_HOST_BASE = "http://127.0.0.1:8767";

/** The known-dummy credential the local-model host accepts on every
 *  model-consuming route (the seeded lmh-dummy value — the host checks
 *  the Bearer's presence, not the secret, while its auth_token is
 *  null). */
const LLM_HOST_BEARER = "lmh-dummy";

/** One fetch with an abort timeout; Error on ANY failure. */
async function llmHostFetch<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${LLM_HOST_BASE}${path}`, {
      ...init,
      headers: {
        ...(init.headers ?? {}),
        Authorization: `Bearer ${LLM_HOST_BEARER}`,
      },
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = (await res.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // non-JSON error body — the status line is the message
      }
      throw new Error(`llm-host ${path} → ${detail}`);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(`llm-host ${path} timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
//  payloads (mirrors server_cluster/llm/data_definitions/admin.py)
// ---------------------------------------------------------------------------

/** One model's lifecycle state — the host's ModelStatus, snake_case as
 *  the host serves it (the same shape rides under GET /v1/models'
 *  `local` extension). */
export interface LlmHostModelStatus {
  model_id: string;
  kind: string;
  state: "not_loaded" | "loading" | "loaded" | "offloaded" | "unloaded" | "error";
  device?: string | null;
  idle_for_s?: number | null;
  error?: string | null;
}

/** GET /v1/models entry — the OpenAI list shape + the `local`
 *  lifecycle extension. */
export interface LlmHostModelEntry {
  id: string;
  object: "model";
  owned_by: string;
  local: LlmHostModelStatus;
}

/** POST /v1/models/offload-all answer — only the models that were
 *  `loaded` and got offloaded by the call. */
export interface LlmHostOffloadAllResult {
  action: "offload-all";
  offloaded: LlmHostModelStatus[];
}

// ---------------------------------------------------------------------------
//  the admin face
// ---------------------------------------------------------------------------

/** GET /v1/models — every configured model with its lifecycle state. */
export async function llmHostListModels(): Promise<LlmHostModelEntry[]> {
  const doc = await llmHostFetch<{ data: LlmHostModelEntry[] }>(
    "/v1/models",
    { method: "GET" },
    10_000,
  );
  return doc.data ?? [];
}

/** POST /v1/models/offload-all — the manual save-memory action: every
 *  loaded model's weights move GPU → CPU now (already-offloaded ones
 *  are skipped; the next request rehydrates transparently). */
export function llmHostOffloadAll(): Promise<LlmHostOffloadAllResult> {
  return llmHostFetch<LlmHostOffloadAllResult>(
    "/v1/models/offload-all",
    { method: "POST" },
    120_000,
  );
}

/** POST /v1/models/{id}/lifecycle — one model's admin action
 *  (load | offload | unload; a cold big-model load takes minutes, so
 *  the load timeout is generous). */
export function llmHostModelLifecycle(
  modelId: string,
  action: "load" | "offload" | "unload",
): Promise<{ model_id: string; action: string; status: LlmHostModelStatus }> {
  const timeoutMs = action === "load" ? 600_000 : 120_000;
  return llmHostFetch(
    `/v1/models/${encodeURIComponent(modelId)}/${action}`,
    { method: "POST" },
    timeoutMs,
  );
}
