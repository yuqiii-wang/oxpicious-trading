/**
 * API client for the Config pages (/api/config) — read + WRITE over the
 * repo's static JSON config trees: the service_config/ and
 * external_request_config/ trees under
 * database/sql/monitoring/static/ and the database/sec_statics/ tree.
 *
 * Plain fetch throughout (NOT the shared fetchJson cache): these pages
 * MUTATE the underlying files, so every read after a save must hit the
 * server — a 10-min TTL cache would serve the pre-save state.
 *
 * The secrets registry (static.secret_config — llm + web_search
 * credentials) is the one DB-backed WRITABLE tab here — same
 * plain-fetch rule applies.
 */

// ---------------------------------------------------------------------------
//  Service config (service_config/ tree)
// ---------------------------------------------------------------------------

/** One service_config/ tree entry: a service's concurrency cap +
 *  supposed-run cadence (mirrored from scripts/*.sh) + its cross-service
 *  parallel-services lists (included = whitelist, empty = unrestricted;
 *  excluded = denylist; entries are rooted dotted prefixes — a scope, a
 *  sub-scope path, or the full service name; more specific wins on
 *  conflict, name > sub-scope > scope, ties to excluded). */
export interface ServiceConfigEntry {
  service: string;
  maxParallel: number;
  /** Optional in-service RUN cap (the deep-agents summary generation
   *  today): how many of the daemon's own runs may execute in parallel
   *  inside the ONE process; null = the code default (one at a time).
   *  Independent of maxParallel, the heartbeat's service-process cap. */
  maxParallelRuns: number | null;
  includedParallelServices: string[];
  excludedParallelServices: string[];
  /** always | daily | monthly | semiannually | once | occasionally |
   *  on_demand (the default.json fallback's value). */
  freq: string;
  schedule: string | null;
  notes: string | null;
  sourceRef: string;
  updatedAt: string;
}

export interface ServiceConfigResponse {
  success: true;
  generatedAt: string;
  rows: ServiceConfigEntry[];
  /** The default.json fallback entry (service "default"). */
  default: ServiceConfigEntry;
}

/** Body of a service-config save — `service` picks the tree file
 *  ("default" edits the fallback); updatedAt is stamped server-side. */
export type ServiceConfigSave = Omit<ServiceConfigEntry, "updatedAt">;

export function fetchServiceConfig(): Promise<ServiceConfigResponse> {
  return fetch("/api/config/service").then((r) => r.json());
}

export function saveServiceConfig(
  entry: ServiceConfigSave,
): Promise<{ success: true; entry: ServiceConfigEntry }> {
  return fetch("/api/config/service", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

/** Remove a service's file so the default fallback applies again. */
export function deleteServiceConfig(service: string): Promise<void> {
  return fetch(`/api/config/service/${encodeURIComponent(service)}`, {
    method: "DELETE",
  }).then(async (r) => {
    if (!r.ok) {
      const body = await r.json();
      throw new Error(body.error ?? `HTTP ${r.status}`);
    }
  });
}

// ---------------------------------------------------------------------------
//  External request config (external_request_config/ tree)
// ---------------------------------------------------------------------------

/** One per-(service, request_type) pacing / anti-bot config row.
 *  resourceType arrives/goes DECODED: http | llm. */
export interface ExtRequestConfigEntry {
  service: string;
  requestType: string;
  resourceType: string;
  sleepSec: number | null;
  sleepJitter: number | null;
  pollIntervalSec: number | null;
  maxRetries: number | null;
  retryBackoffSec: number | null;
  timeoutConnectSec: number | null;
  timeoutReadSec: number | null;
  rotateBrowserProfile: boolean;
  addRandomParam: boolean;
  enableHostTracking: boolean;
  hostBlockCooldownSec: number | null;
  /** Playwright flows' post-navigation settle wait (null = the flow keeps
   *  its own behavior). */
  settleSleepSec: number | null;
  /** The row's endpoint inventory {role: url} (null = none). */
  urls: Record<string, string> | null;
  notes: string | null;
  sourceRef: string;
  updatedAt: string;
}

export interface ExtRequestConfigResponse {
  success: true;
  generatedAt: string;
  rows: ExtRequestConfigEntry[];
  /** The default.json fallback rows (repo-wide 60s anti-bot cooldown). */
  defaultRows: ExtRequestConfigEntry[];
}

/** Save ONE service's whole row list — an empty list DELETES the
 *  service's file so the fallback applies again. */
export function saveExtRequestConfig(
  service: string,
  rows: Array<Omit<ExtRequestConfigEntry, "service" | "updatedAt">>,
): Promise<{ success: true; service: string; rows: ExtRequestConfigEntry[] }> {
  return fetch("/api/config/ext-request", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service, rows }),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

export function fetchExtRequestConfig(): Promise<ExtRequestConfigResponse> {
  return fetch("/api/config/ext-request").then((r) => r.json());
}

// ---------------------------------------------------------------------------
//  Security classification statics (database/sec_statics/ tree)
// ---------------------------------------------------------------------------

export type SecStaticsArea =
  | "rules"
  | "importance"
  | "owners"
  | "catalog"
  | "indices"
  | "meta";

export interface SecStaticsFile {
  /** Tree-relative POSIX path — the read/write key. */
  path: string;
  area: SecStaticsArea;
  /** Rebuilt wholesale by builds.classification — edits don't survive. */
  generated: boolean;
  sizeBytes: number;
  updatedAt: string;
  /** importance_rules rank files only — curated code-list summary parsed
   *  from the file (shown inline in the path table). */
  codesCount?: number;
  sampleCodes?: string[];
}

export function fetchSecStaticsFiles(): Promise<{
  success: true;
  generatedAt: string;
  files: SecStaticsFile[];
}> {
  return fetch("/api/config/sec-statics").then((r) => r.json());
}

export function fetchSecStaticsFile(
  path: string,
): Promise<{ success: true; file: SecStaticsFile; text: string }> {
  return fetch(
    `/api/config/sec-statics/file?path=${encodeURIComponent(path)}`,
  ).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

/** Write one file's text verbatim (server JSON-validates). */
export function saveSecStaticsFile(
  path: string,
  text: string,
): Promise<{ success: true; file: SecStaticsFile }> {
  return fetch("/api/config/sec-statics/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, text }),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

// ---------------------------------------------------------------------------
//  Orchestration steps (scripts/orchestration/ tree — read-only)
// ---------------------------------------------------------------------------

/** One orchestration step: the runner executes a tier's steps one by
 *  one in step-number order (strictly serial — canBeParallel is false
 *  for every step today). */
export interface OrchestrationStep {
  step: number;
  script: string;
  args: string[];
  canBeParallel: boolean;
  note: string | null;
}

/** One tier script's steps (<tier>.json ↔ scripts/<tier>.sh). */
export interface OrchestrationTier {
  /** File stem, e.g. "builds_daily". */
  tier: string;
  /** Leading segment of the tier name, e.g. "builds". */
  domain: string;
  /** Trailing cadence segment, e.g. "daily" ("" when the name carries
   *  no known freq suffix). */
  freq: string;
  sourceRef: string;
  steps: OrchestrationStep[];
}

export function fetchOrchestrationConfig(): Promise<{
  success: true;
  generatedAt: string;
  tiers: OrchestrationTier[];
}> {
  return fetch("/api/config/orchestration").then((r) => r.json());
}

/** One seeded row of static.keyword_maps — the keyword → category map
 *  (database/sql/static/keyword_maps/ seed files; labels decoded at the
 *  API boundary). */
export interface KeywordMapEntry {
  keyword: string;
  /** industry | market | broadmarket | skip | adj | adv | adv_noun |
   *  adv_verb. */
  category_type: string;
  /** industry_id, the decoded kw_market / kw_theme label, or null for
   *  skip and component rows. */
  category: string | null;
  category_code: number | null;
  /** noun | verb | noun_verb | function. */
  parts_of_speech: string;
  /** A preceding negator (没有/并未/no/not/…) flips the keyword. */
  is_negation: boolean;
  /** zh | en. */
  language: string;
}

export function fetchKeywordMaps(): Promise<{
  success: true;
  generatedAt: string;
  count: number;
  entries: KeywordMapEntry[];
}> {
  return fetch("/api/config/keyword-maps").then((r) => r.json());
}

// ---------------------------------------------------------------------------
//  Deep agent run limits (deep_agent_config.json)
// ---------------------------------------------------------------------------

/** The deep agent's RUN LIMITS — how many and how large the LLM
 *  requests of one run may be. Mirrors the API payload (camelCase);
 *  the file itself keeps the snake_case mirror of
 *  llm_agents/deep_agents/limits.py::RunLimits, which re-reads it per
 *  run — a save applies to the NEXT run, no daemon restart. */
export interface DeepAgentLimits {
  maxConversationSteps: number;
  minConversationSteps: number;
  maxStepsCap: number;
  recursionLimit: number;
  runTimeoutSec: number;
  llmRequestTimeoutSec: number;
  maxPlanNodes: number;
  maxDelegatedSteps: number;
  planRepairRounds: number;
  planProposalBatches: number;
  maxTotalPlanSteps: number;
  maxForkChildren: number;
  maxParallelDelegatedSteps: number;
  maxConcurrentSubagents: number;
  maxSpawnDepth: number;
  /** Per spawned child's wall clock in seconds; null = unbounded. */
  subagentTimeoutSec: number | null;
  maxOutputTokens: number;
  maxOutputRepairs: number;
  findingMaxChars: number;
  findingsBlockMaxChars: number;
  promptEagerRows: number;
  promptEagerChars: number;
  payloadGuardChars: number;
  payloadEntryMaxChars: number;
  payloadPreviewChars: number;
  payloadPreviewItems: number;
  toolResultMaxChars: number;
  /** The supervisor repair lane's knobs. */
  supervisorPollIntervalSec: number;
  supervisorRepairGraceSec: number;
  supervisorMaxNodeRetries: number;
  supervisorMaxRepairsPerRun: number;
  supervisorRetryThrottleSec: number;
  notes: string | null;
  sourceRef: string | null;
  updatedAt: string;
}

export function fetchDeepAgentLimits(): Promise<{
  success: true;
  generatedAt: string;
  limits: DeepAgentLimits;
}> {
  return fetch("/api/config/deep-agent").then((r) => r.json());
}

/** Body of a limits save — updatedAt is stamped server-side. */
export type DeepAgentLimitsSave = Omit<DeepAgentLimits, "updatedAt">;

export function saveDeepAgentLimits(
  limits: DeepAgentLimitsSave,
): Promise<{ success: true; limits: DeepAgentLimits }> {
  return fetch("/api/config/deep-agent", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(limits),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

// ---------------------------------------------------------------------------
//  Secrets config (static.secret_config — DB-backed, the ONLY writable
//  DB config tab; Python consumers read it at startup)
// ---------------------------------------------------------------------------

/** The closed secret_type vocabulary (mirror of
 *  static.label_dim 'secret_type'; the provider column's label domain
 *  is SELECTED BY the type — the audit pattern: llm →
 *  ext_llm_provider, web_search → ext_http_source). */
export const SECRET_TYPES = ["llm", "web_search"] as const;
export type SecretType = (typeof SECRET_TYPES)[number];

/** One credential row. NO key material ever crosses to the client:
 *  `apiKeyHint` is the masked preview (•••••••• + last 4) the server
 *  stored next to the AES-256-GCM ciphertext. */
export interface SecretEntry {
  secretId: number;
  secretType: SecretType;
  secretTypeCode: number;
  /** Decoded provider label (domain selected by secretType: llm →
   *  zhipu | other; web_search → zhipu | zhihu | other). */
  provider: string;
  providerCode: number;
  /** llm: the model id (required); web_search: the engine or null. */
  modelName: string | null;
  /** llm only: max context window in tokens; null = unspecified. */
  contextLength: number | null;
  /** The credential's endpoint; null = the consumer's built-in default. */
  apiUrl: string | null;
  apiKeyHint: string | null;
  hasApiKey: boolean;
  isActive: boolean;
  notes: string | null;
  updatedAt: string;
}

export interface SecretListResponse {
  success: true;
  generatedAt: string;
  count: number;
  rows: SecretEntry[];
}

/** Body of a secret save. `apiKey` is the tri-state secret field:
 *  omitted = keep the stored key (create: no key) · null = clear it ·
 *  a string = set/replace (the server encrypts at rest; the plaintext
 *  is never stored in the clear and never returned). */
export interface SecretSaveBody {
  secretType: SecretType;
  provider: string;
  modelName: string | null;
  contextLength: number | null;
  apiUrl: string | null;
  apiKey?: string | null;
  isActive: boolean;
  notes: string | null;
}

export function fetchSecrets(): Promise<SecretListResponse> {
  return fetch("/api/config/secrets").then((r) => r.json());
}

export function createSecret(
  body: SecretSaveBody,
): Promise<{ success: true; entry: SecretEntry }> {
  return fetch("/api/config/secrets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (r) => {
    const data = await r.json();
    if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
    return data;
  });
}

export function updateSecret(
  secretId: number,
  body: SecretSaveBody,
): Promise<{ success: true; entry: SecretEntry }> {
  return fetch(`/api/config/secrets/${secretId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (r) => {
    const data = await r.json();
    if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
    return data;
  });
}

/** Remove one credential row entirely — the encrypted key goes with
 *  it (the row's ✕ action; the UI confirms before calling). */
export function deleteSecret(secretId: number): Promise<void> {
  return fetch(`/api/config/secrets/${secretId}`, {
    method: "DELETE",
  }).then(async (r) => {
    if (!r.ok) {
      const data = await r.json();
      throw new Error(data.error ?? `HTTP ${r.status}`);
    }
  });
}

/** The connection test's verdict — status/latency/snippet only, never
 *  key material. `keySource` says which key the server used: the typed
 *  one (form), the row's decrypted one (stored), the .env fallback
 *  (env), or none. */
export interface SecretTestResult {
  ok: boolean;
  statusCode: number | null;
  latencyMs: number | null;
  endpoint: string | null;
  keySource: "form" | "stored" | "env" | "none";
  model: string | null;
  replySnippet: string | null;
  error: string | null;
}

/** Test input — the CURRENT form values; `apiKey` must be a TYPED
 *  plaintext or omitted (never the field's display mask: omitted means
 *  "use the stored key"). */
export interface SecretTestBody {
  secretId?: number;
  secretType: SecretType;
  provider: string;
  modelName: string | null;
  apiUrl: string | null;
  apiKey?: string | null;
}

export function testSecret(
  body: SecretTestBody,
): Promise<{ success: true; result: SecretTestResult }> {
  return fetch("/api/config/secrets/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (r) => {
    const data = await r.json();
    if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
    return data;
  });
}

// ---------------------------------------------------------------------------
//  Knowledge graph (llm.kg_entities / llm.kg_edges — read-only)
// ---------------------------------------------------------------------------

/** One entity node of a namespace's subgraph — the canonical
 *  (namespace, entity_type, name) row (DDL
 *  database/sql/llm/13_kg_hypergraph.sql). */
export interface KgEntity {
  entityId: number;
  name: string;
  entityType: string;
  description: string | null;
  tags: string[];
}

/** One hyperedge with members RESOLVED to names, head-first
 *  (members[0] = subject; cardinality 2 degenerates to a plain graph
 *  edge). A member id that misses the entity map degrades to name null
 *  — never a failed request. */
export interface KgEdge {
  edgeId: number;
  relation: string;
  subRelation: string | null;
  weight: number | null;
  members: Array<{
    entityId: number;
    name: string | null;
    entityType: string | null;
  }>;
}

export interface KgGraphResponse {
  success: true;
  generatedAt: string;
  namespace: number;
  entities: KgEntity[];
  edges: KgEdge[];
}

/** The shipped kg_namespace codes (mirrors kg.service's KG_NAMESPACES
 *  and the deep-agent kit's — kg_namespace is not a label_dim domain
 *  yet). */
export const KG_NAMESPACES = [
  { code: 0, label: "DB semantics", hint: "tables/views + how they relate" },
  { code: 1, label: "Prompts", hint: "skills & plan prompts + how they relate" },
] as const;

export function fetchKgGraph(namespace: number): Promise<KgGraphResponse> {
  return fetch(`/api/config/kg?namespace=${namespace}`).then(async (r) => {
    const body = await r.json();
    if (!r.ok || !body?.success) {
      throw new Error(body?.error ?? `HTTP ${r.status}`);
    }
    return body as KgGraphResponse;
  });
}
