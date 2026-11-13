---
name: deep-agent-blueprint
description: >
  Blueprint of the local deep agent (llm_agents/deep_agents) — the current
  architecture map, distilled best practices (LangChain Deep Agents, Anthropic
  multi-agent research system, context engineering, DeepSeek Harness +
  Super-agent), and the ranked gap roadmap (memory, critic, evals, effort
  scaling, HITL/resume, artifact filesystem). Use for ANY change to
  llm_agents/deep_agents (core loop, planning, langgraph, delegation, prompts,
  server), ANY "improve the deep agent / add memory / verification /
  evaluation / resume" request, Ask AI analysis-summary agent work, or ANY
  comparison of agent architectures — even if the words "deep agent" are
  never said. Study distilled 2026-11-01; re-study before acting on stale
  external claims.
---

# Deep agent blueprint (`llm_agents/deep_agents`)

2026-11-13 NAMING NOTE — fork renamed to spawn, project-wide: the
fork machinery (ForkHooks/SpawnHooks, ForkBroker/SpawnBroker,
fork addenda/spawn addenda, `node_fork`/`node_spawn`,
`route_fork`/`route_spawn`, config knobs `max_spawn_children` /
`sandbox_spawn_after_failures` / `sandbox_spawn_max` /
`jev_route_max_spawns`, the `agent_step_type` 1 label, the prompt
keys `deep-agent-spawn-plan-addendum` / `deep-agent-spawn-delegate-addendum`,
the `spawned-evidence-search` skill, UI ids `spawn-<state_id>`) always
WAS spawn logic — a fresh-context child sub node — so every fork name
became spawn. Historical artifacts keep their recorded fork names:
old `llm.deep_agent_states.node` slugs (`fork_<topic>`), old
`llm.deep_agent_aux_steps.lane` values (`node_fork` / `route_fork`),
historical run references below (e.g. fork 2825), and the demised
dataset's markers.

Distilled study of how the local deep agent harness is built, how it
compares to the published state of the art, and what to build next. Read
this file fully before changing the harness or planning an improvement;
dip into `references/` when you need the detail behind a claim:

- `references/local-architecture.md` — full module map with file paths,
  the layering rules, persistence model, and known in-code roadmap notes.
- `references/best-practices.md` — LangChain Deep Agents pillars, Anthropic
  multi-agent research lessons + context engineering, verification-loop
  patterns.
- `references/deepseek-super-agent.md` — DeepSeek Harness (dsh) concepts and
  the 2026-10 Super-agent design ("one router, many workers"), with the
  measured results that justify each principle.

## The harness today (2026-11-02)

Self-built harness, deliberately NOT LangChain `deepagents`, modeled on
Claude Code / DeepSeek Harness / WorkBuddy (design notes in
`llm_agents/deep_agents/__init__.py`). Layers, strict direction:

- `core/` — the runtime: one message list, one OpenAI-style tool-calling
  loop per step (`core/loop.py::DeepAgent.run`), sibling tool calls via
  `asyncio.gather`, MCP client (`core/mcp.py`), docker py-sandbox client
  (`core/sandbox.py`), ZhiPu provider (`core/provider.py`, glm-5.3-flash).
  2026-11-11: the WRITE-RUN-FIX LOOP BREAKER — `_note_sandbox_outcome`
  tracks consecutive failed sandbox-tool outcomes (a hard MCP/exception/
  cancel error, or a `sandbox_run` whose JSON reports non-zero
  `exit_code`; a successful run resets the streak, successful
  writes/edits are neutral) and at `sandbox_failure_nudge_threshold`
  (default 3) the loop injects ONE user nudge (patch the broken block
  with sandbox_edit_file, fail-fast per-block try/except, fall back to
  db_run_query) — at most `sandbox_failure_max_nudges` (default 1, 0
  disables) per conversation, knobs in `deep_agent_config.json`
  (Python-only keys, the pre-existing panel-save wipe hazard applies).
  2026-11-11 (b) the breaker's FRESH-CONTEXT ESCALATION (the user
  directive "for twice syntax err sandbox script run, spawn a fresh
  context node to save tokens, and parent node await it"): after
  `sandbox_spawn_after_failures` (default 2) consecutive TRACEBACK-class
  script failures (exit≠0 with Traceback/SyntaxError in the output —
  timeouts never count; `_remember_fail_line` keeps the last 8
  exception lines as the fix history) the loop spawns ONE repair
  subagent — FOREGROUND, the parent awaits (`_maybe_spawn_repair` →
  `_run_subagent({background: False})`, the same hooks a model-called
  spawn rides, so the child state row registers in the plan tree) —
  whose whole context is `_repair_handoff`'s compact handoff (run goal
  + failing script path + fix history + patch method), never the
  parent transcript; the child's answer rides back as an injection
  user message and both streaks reset; at most `sandbox_spawn_max`
  (default 1, 0 disables) per conversation; depth cap /
  allow_subagents=False seats fall back to the nudge lane. The repair
  child inherits the SHARED sandbox session (read the script +
  `sandbox_edit_file`, never rewrite). Stub-tested
  `temp_scripts/test_sandbox_spawn_repair.py` (20 checks — the
  fresh-child context is 2 messages, budget, timeout-class isolation,
  depth-cap and allow_subagents fallbacks); the nudge lane's scenarios
  run with the spawn disabled
  (`temp_scripts/test_sandbox_failure_nudge.py`, 13 checks).
  2026-11-13 THE SYSTEM ONE ROUTER (`core/loop.py::_route_response` +
  the `route_spawn` / `route_join` / `failure_probe` templates in
  `langgraph/decision/`): after EVERY LLM response that proposes
  sandbox write/run/edit calls the injected jev engine routes the NEXT
  work — INFERENCE (continue the conversation's own chain, the default
  on every no/below-floor/missing read), SPAWN (a self-contained
  scripting sub-assistant task: ONE foreground child with
  `SubagentRequest.role='scripting'` and `_scripting_handoff`'s compact
  context — goal + the response's plan + the proposed calls — takes
  over, the parent's calls never execute, synthetic tool results +
  the child's injection keep the transcript protocol-shaped), or JOIN
  (a MESSY scripting node — fail streak or a mostly-spent budget —
  gets ONE nudge to write its handback; a tool-calling reply after the
  nudge force-returns `[Joined the parent chain …]`; only
  scripting-role nodes are join-eligible, and they are NEVER
  router-spawned — the no-re-spawn rule). ROLE rides the request onto
  the child `DeepAgent(node_role=…, decision_engine=…)` (both threaded
  in `_execute_subagent` since 2026-11-13 — the child's own responses
  route/join); the breaker's repair spawn stamps 'scripting' too. ON
  FAILURE-JOIN: a foreground scripting child that fails triggers the
  FAILURE-PROBE lane (`_maybe_probe_failure`) — STRONGLY PROMPTED
  (every jev read spawns EXCEPT a confident no; missing-method /
  exception = no read = no probe), bounded `jev_route_probe_max` (1)
  per conversation — an INVESTIGATOR child (`role='investigator'`)
  whose `_probe_handoff` carries the AUTHORIZATION: the failed node's
  state id + llm chain id (stamped onto `SubagentRun` by the spawn
  hooks' input hook) + goal + error, inspected through the
  chain-scoped search_thread_context and the shared sandbox. Knobs
  `jev_route` / `jev_route_max_spawns` (3) / `jev_route_join` /
  `jev_route_probe_max` in deep_agent_config.json + the `JEV_ROUTE=0`
  kill-switch; reads are missing-method-safe (`_engine_read`) so a
  pre-router injected engine never crashes. Stub-tested
  `temp_scripts/test_jev_router.py` (22 checks); the three templates'
  scenarios live in `test/dummy_langgraph/features/02_decisions.feature`
  (all 10 live expectations measured matching the real model,
  `temp_scripts/test_dummy_langgraph.py` 31 checks). NOTE: the
  dynamics/scenarios/jev-walk probes + the two breaker test files are
  PRE-EXISTING casualties of the 2026-11-12 (e) dummy REBUILT-SIMPLE
  refactor (they import the demised `FEATURES`/`DummyReplyBook`/
  `ReplySpec` names) — dead at HEAD before the router landed.
  2026-11-13 (b) THE TOOL-USAGE ROUND (mined from the same-day run
  4597f052's recorded traffic — 11 step-limit deaths on one study):
  (1) THE DETERMINISTIC HANDBACK RESERVE (`core/loop.py`): scripting
  forks died at the step cap AFTER their work was done — 2825/2889
  ticked their plan nodes completed, then the cap RuntimeError
  discarded the node and the parent re-spawned the same study (the jev
  join read is affirm-only and orderly bookkeeping reads as "not
  messy"). One step before the cap a scripting node gets the
  `[Handback reserve]` nudge (pure budget arithmetic, no model
  judgment); a text reply ships through the answer gate, a
  still-calling final reply is force-joined by the existing post-nudge
  lane. Main-path nodes keep the cap RuntimeError (the step lane's
  settle + supervisor diagnosis). Stub-tested
  `temp_scripts/test_handback_reserve.py` (12 checks incl. the
  force-join + main-node preservation). (2) THE OUTPUT-CEILING CUT: a
  reply that hit `max_output_tokens` MID tool-call shipped a cut
  arguments JSON and the loop's bare "Unterminated string" error never
  named the cause — `core/provider.py` now surfaces
  `finish_reason` on the message dict and `_execute_call`'s cut-args
  error teaches the resend remedy (split the write / patch one block
  with sandbox_edit_file); providers without the key fail soft.
  (3) The `sandbox_edit_file` read guard compared the file's BYTE size
  against `len(content)` — a CHAR count — so any multi-byte UTF-8 file
  (one em-dash sufficed) was false-rejected as "beyond the 1 MB read
  cap" (20 occurrences in the run, files 466 B–10 KB), forcing
  whole-file rewrites; the guard now checks the byte cap + the
  runner's literal truncation marker (the same round fixed
  `update_plan_step`'s rejection to teach the add_nodes-first remedy —
  fork 2825 spent ~12 steps discovering it). (4) THE REPEAT GUARD
  (`core/loop.py`): the identical call, unchanged, right after a
  failed result gets `[Repeat guard]` stamped onto its result
  (per-fork failure walks: 2893's identical edit ×2, 2905's identical
  run ×2 with nothing changed between; the legit edit→rerun of the
  same script path is never stamped — an intervening different call
  resets the signature). Stub-tested in
  `temp_scripts/test_handback_reserve.py` (15 checks; the study also
  cleared `timeout_s` — a REAL wired knob — and the update_plan title
  validator as by-design). (5) THE DATASET: the fixes walk E2E on the
  dummy — `test/dummy_llm/features/00_tool_usage.feature` +
  `temp_scripts/probe_dummy_langgraph_tool_usage.py` (four run_thread
  walks: HANDBACK RESERVE 预算耗尽前回交 / OUTPUT-CEILING CUT 参数截断
  via the provider's NEW `the reply calls "…" with its arguments cut`
  Then form — the DocString is the intended JSON, the provider emits
  it chopped + finish_reason "length" — / REPEAT GUARD 原样重试 /
  REPAIR HANDOFF — the breaker's fresh-context repair child's first
  dummy face since the dynamics demise, keyed "[Repair handoff" and
  ordered before the repeat-child faces because the handoff embeds
  the failed goal; the parent-only discriminator
  "[Fresh-context repair agent took over]" (the injection text)
  negatives the child faces out of the parent's post-injection
  conversation — the same markers otherwise reach it through the
  embedded goal; the walk runs THREE generations deep (parent →
  child A fails twice → A's breaker spawns B keyed "[Repair handoff"
  → B patches/reruns/hands back → A's post-injection turn hands the
  evidence up via `repair-child-handup` → the parent folds via
  `repeat-step-fold-repair`).
- `tools/` — the TOOL LAYER (2026-11-02): every model-callable capability
  (beside the external MCP tools) as an OBJECT — a `Tool` subclass per
  tool (schema + `run` handler + its `llm.ai_prompts` row, prompt_type
  4 `tool`: seeded from the classes' `DEFAULT_DESCRIPTION` constants by
  `static/ai_prompts/10_tool_prompts.sql`, overriding the built-in
  schema text fail-soft per run via `store.fetch_tool_prompts`),
  grouped into `ToolKit`s the runner mounts per conversation role:
  `SandboxToolSet` (2026-11-11 FIVE sandbox_* tools over one `Sandbox`
  client: write/run/read/list plus `sandbox_edit_file` — the
  patch-one-block tool, read → verify → replace → write composed over
  the middleware's read/write actions with a truncated-read guard;
  built for run e1c366bd's write→run→fail→rewrite failure mode — nine
  whole-script rewrites chasing eight SQL bugs until the user stopped
  it; `sandbox_write_file`'s description teaches fail-fast per-block
  try/except + edit-don't-rewrite),
  `MainPlanToolKit` / `SpawnPlanToolKit` (the plan-mutation tools over a
  `PlanAuthority` + `ThreadSearchTool`), `StatsToolSet` (2026-11-02:
  the six `stats_*` study tools over the daily bars — ma/rsi/macd/roc/
  volume/range; house conventions, plain numpy on the read replica,
  precomputed view MA columns reused when they cover the window; the
  runner mounts it on every step conversation and the hooks stamp it
  on every spawn), `AdHocRequestToolKit` (2026-11-10:
  `ad_hoc_llm_request` — the model-facing face of the package-root
  `ad_hoc_llm_request` module, the ONE independent fresh-context
  request lane: every ask builds a two-message `[system, user]`
  context from its arguments alone (tools=None, nothing carried, the
  reply collapsed + capped at `AD_HOC_MAX_REPLY_CHARS` 4000) and runs
  under its OWN one-row request-log chain via an explicit fresh
  `bind_request_chain(uuid)` — never the ambient conversation's
  chain, so no incremental-payload dedup against a transcript and no
  cache-token pollution in the `llm_usage.sql` rollup;
  `AdHocRequestTool` lets the model fire a self-contained side
  question instead of working it through its own history (put
  everything into `prompt`, keep the answer compact), fail-soft error
  text per the never-raise contract; `default()` resolves the active
  `llm_config` provider for code callers outside a run; mounted
  beside `load_prompt` on EVERY conversation — the step deps'
  make_tools + the spawn extras lane, the degraded lane, the output
  nodes, the step/review lanes; seeded row `ad_hoc_llm_request|ad_hoc`
  in `10_tool_prompts.sql`; offline `temp_scripts/test_ad_hoc_llm_
  request.py` (29 checks) + HTTP-sim `temp_scripts/probe_ad_hoc_
  request_live.py` asserting the own-chain landing in
  `llm.llm_requests`), `KgToolSet` (2026-11-05: `kg_search` +
  `kg_expand`
  — the knowledge graphs `llm.kg_entities`/`llm.kg_edges` as SEMANTIC
  SEARCH INDEXES with a `namespace` argument: 0 = DB semantics (one
  entity per table; agents find tables by business meaning —
  bilingual trader vocabulary, never a schema dump) and 1 = the
  prompt catalog (one entity per cataloged skill/plan row — WHICH
  skill teaches a domain, then `load_prompt` it run-scoped);
  2026-11-08 namespace 2 = data & labels (one entity per
  static.label_dim SMALLINT vocabulary — tags = the code labels —
  plus the tables its typed columns live in, edges labeled_by/
  for_<column>): the CODE-WORD lane that locates tables by their
  typed vocabulary (the macd-miss fix — the family name is a label
  code, not table vocabulary), GENERATED by
  `temp_scripts/generate_kg_data_labels.py` into
  `database/sql/llm/static/knowledge_graphs/data_and_labels/`; the
  kg_search DEFAULT lane (namespace omitted) now searches ns 0 AND
  ns 2 TOGETHER (fail-soft per lane; ns2 capped leaner at 6 nodes,
  merged hint teaches the static.label_dim decode);
  2026-11-06 the KG domain MIGRATED to the INDEPENDENT module
  `llm_agents/kg/` (imports nothing from deep_agents): `graph.py`
  the pure namespace-blind traversal engine — seed recognition over a
  CJK-bigram + latin-word token index with IDF weighting → spreading
  activation (per-hop decay, per-(relation, direction) factors,
  fan-out-normalized energy, sub-relation agreement boost,
  beam-bounded frontier) → blended rank → the packed subgraph with
  provenance lines; a RELEVANCE FLOOR scales results to the
  question's specificity (tail seeds a top hit dwarfs drop;
  propagated rows are floored on the global scale), and the edge
  packing carries a JOIN-HINT tier — the chosen nodes' strongest
  outward edges (ns0 `joins_on` seeds
  `db_semantics/17_sql_join_paths.sql`: the repo's own SQL join
  paths, every edge citing its file); `service.py`
  `KnowledgeGraphService` (the process-wide `KNOWLEDGE_GRAPHS`
  singleton) — the TTL-cached per-namespace loader + the search /
  expand flows; `tools/kg_tools/kit.py` keeps only the THIN ADAPTERS
  (schema + argument coercion + agent-flow hints, mounted beside
  StatsToolSet on every step conversation, every spawned child, and
  the degraded execute lane); ns1 seeded by
  `database/sql/llm/static/knowledge_graphs/prompts/01_prompt_catalog.sql`
  (31 rows / 15 edges); the incremental contract is asserted live by
  `temp_scripts/verify_kg_incremental.py`;
  2026-11-09 the ns0 READ-SIDE CORPUS: every `database/sql_views`
  template is a damped ns0 `view` entity (slash-path name, header-
  harvested description, `vocabulary.seed_type_damp` 0.5 so tables
  lead) linked to the relations it reads by `reads_from` edges —
  DERIVED by the Python static rules (`llm_agents/kg/static_rules/`,
  the common dir scanner `sources.py`, sync `python -m
  llm_agents.kg.static_rules`, all writes through
  `database/sql_writes/llm_agents/kg/static_rules/` action files) —
  and a live kg_search attaches a bounded `sql views reading this:`
  block under its top-3 located tables (`KnowledgeGraphService.
  _sql_view_blocks` / `KnowledgeGraph.related_views`), the
  deterministic answer a hub table's 100+ reading templates need; the
  same day the READ-TIME FRESHNESS GATE landed (`kg/static_rules/
  freshness.py::RuleFreshnessGate`, hooked in
  `service.graph`'s reload path): the corpus stat scan (file count +
  MAX mtime) vs the `llm.kg_rule_syncs` fingerprint row (DDL
  `database/sql/llm/kg_rule_syncs.sql`) — stale (files newer /
  count changed / no row) → the rule's idempotent sync runs INLINE
  before the read, fail-soft to the stored graph, so a template edit
  reaches kg_search at the next graph-TTL reload with no daemon
  restart and no manual runner run; 2026-11-09 (b) the ns0 authored
  TABLE entities migrated out of the seed INSERTs into a second
  corpus rule (`db_semantics_tables`; 2026-11-10 the payloads moved
  INTO the DDL — the former `database/kg_tables/` parallel corpus was
  deleted): ONE TABLE = ONE FILE under `database/sql/`, the entity
  name from the file's own qualified `CREATE TABLE`, the
  `-- description:`/`-- tags:` payload in the file's leading banner,
  ledger-owned via `llm.kg_rule_entities` (a deleted payload prunes
  the entity AND every edge touching it) — the seeds keep only the
  edge INSERTs + the DB view entities, and `apply_kg.py` runs the
  rules' sync between DDL and seeds so fresh installs resolve;
  the KG CHAIN CACHE's TWO LAYERS (2026-11-06):
  `llm.kg_queries` (`database/sql/llm/kg_queries.sql`,
  `kg/query_log.py::QueryLog`)
  is the LOG / TWO-HIT EVIDENCE table — every successful traversal's
  chain relogged fail-soft (DELETE + INSERT, one live chain per
  (query, namespace), PK (namespace, chain_id, edge_id), query
  indexed: (query, namespace) btree + pg_trgm GIN) — and
  `llm.kg_hotpaths` (`database/sql/llm/kg_hotpaths.sql`,
  `kg/hotpaths.py::HotpathCache`; same row shape + hit_count,
  CHECK (hit_count >= 2)) is the PROMOTED REPLAY layer: kg_search
  replays ONLY from hotpaths — `lookup` exact-matches (query,
  namespace) with entity_hash = `KnowledgeGraph.entity_digest`
  (md5 over the entities' descriptions — an edit/add/remove
  invalidates, renames and tag/edge edits do not) and bumps
  hit_count; a miss runs the traversal, whose fresh chain
  `find_identical_chain` matches against EVERY kg_queries chain under
  the same (namespace, entity_hash) — ANY query text, edge-for-edge
  in depth order, BEFORE the relog — and an identical twin (the chain
  produced TWICE) promotes the query into kg_hotpaths at hit_count 2;
  replays render via `render_replay` against the live graph (hops /
  max_results are NOT identity, an exact query match copies the
  chain); stub-tested `temp_scripts/test_kg_search.py`, live-probed
  `temp_scripts/probe_kg_search_live.py` and
  `temp_scripts/probe_kg_query_log.py` (the two-hit contract)); since
  2026-11-07 the hotpaths have a THIRD consumer beside the kg_search
  replay lane + the hit_count bumps — the prompt loader's hotpath
  corroboration rung (`planning/kg_prompts.py::HotpathPromptProbe`,
  read-only at plan time; see the skills/ bullet), and the
  delegation schema
  objects (spawn/collect HANDLERS stay loop methods — their supervision
  state lives in the loop). `base.BuiltinTool` (the loop's runtime pair)
  moved here; `core.tools` re-exports it. 2026-11-06: the REPAIR LANE —
  (2026-11-11 refactored onto the EVENT-QUEUE PICKER, see the
  supervisor bullet below; `supervisor/repair.py` keeps the transient
  classifier + the Action parser + the structural policy gate +
  RepairChannel's unchanged lane facade, and the decision body lives
  in `supervisor/workers.py::RepairWorker` dispatched by the
  `supervisor/poller.py` picker): the step lane, on a
  root-path failure, opens a BOUNDED PAUSE WINDOW before settling the
  doomed tail (`runner/step_lane.py`'s repair gate — signal, await
  verdicts, refresh the tree); the picker wakes instantly on the
  queue's NODE_FAILED offer (or runs its `supervisor_poll_interval_sec`
  belt scan), classifies IN CODE
  (rate limit / timeout / upstream 5xx / zhipu 1701|1234 / request
  budget = transient; 1113 balance, step cap, user stop = never), and
  only a transient, budgeted candidate spends ONE bounded agent call
  whose `Action:` line (fail-soft parsed from the RAW reply) gates the
  retry through `should_retry` (per-node `supervisor_max_node_retries`
  + per-run `supervisor_max_repairs_per_run`); a retry RE-OPENS the
  failed row (`planning/store.py::reopen_state`, guarded failed→
  pending, error/finished_at cleared) and the lane's normal routing
  re-picks it next hop — the run resumes. Non-repairable failures
  answer no-action with ZERO LLM (the final sweep reviews them);
  repairs are deduped from the sweep via the channel's handled set.
  Knobs in `deep_agent_config.json`; stub-tested
  (`temp_scripts/test_supervisor_repair.py`) and probed e2e on the
  DUMMY LLM (`temp_scripts/probe_supervisor_repair_live.py` +
  `temp_scripts/probe_supervisor_repair_fallback_live.py`).
  2026-11-12 — THE SYSTEM ONE FAST PATH (`langgraph/decision/`,
  jev primary):
  when an engine is injected the RepairWorker asks the resident jev
  model FIRST (one noul "resume?" read, ~0.1s, zero provider spend);
  only a CONFIDENT YES applies the retry (the verdict persists as a
  supervisor-review row) — the read can NEVER veto: a no/below-floor/
  unavailable read falls through to the generative ask untouched (the
  2026-11-12 serving probes measured the operational reads near 0.5 —
  a decisive-no is noise and was rejected as a retry-killer; the
  whole-process probe proved the point, p_no 0.63 would have no-action'd
  a transient timeout the lane exists to retry). The loop's
  repair-spawn breaker gets the sibling gate (a confident no declines
  the spawn — bounded, the nudge lane judges). Knobs: `jev_decisions`
  / `jev_min_confidence` / `jev_model` + the `JEV_DECISIONS=0` env
  kill-switch (the generative-lane probes set it). Stub-tested
  `temp_scripts/test_jev_decision.py` (12 checks), live-probed
  `temp_scripts/probe_jev_decision_live.py`; see `jev-systemone`.
  KNOWN DRIFT (pre-dates the lane): the two repair probes'
  RESUMED scope_anchor conversation burns its step cap — the standing
  `tool_demo_thread_search` rule matches the resume block's
  search_thread_context needle every turn (the scripted whole-process
  trio intercepts its resumed conversation first and is clean; the
  fix direction is a state-aware thread-search rule, the sandbox
  rule's precedent).
  2026-11-06 (b) — THE JOIN PAUSES FOR EVERY FAILURE + the IGNORE
  verdict + THROTTLED rate-limit retries: `failed_rows` now collects
  EVERY failed member (biz step, wave member, REVIEW reviewer
  included) — the lane pauses at the join (every parallel before-node
  has settled; the barrier holds the resume) while the supervisor
  decides among THREE actions: `retry-step` (a RATE-LIMIT-class retry
  — 429/1302/1701/速率限制, `repair.is_rate_limited` — additionally
  waits `supervisor_retry_throttle_sec` (default 45) INSIDE the
  window before the hop re-runs; an immediate re-run re-hits the hot
  account, observed live), `ignore-step` (the failed node's work is
  dispensable — `planning/store.py::ignore_state` settles the failed
  row skipped wearing the supervisor's note and the run resumes PAST
  it; for a biz step this is new: the path no longer ends), and
  `no-action` (old semantics: biz step ends the path, reviewer
  failure proceeds without it). Every lane-signalled failure gets ONE
  agent ask (the `asked` set dedupes — a budgetless re-occurrence
  fast-paths no-action with zero LLM); biz parallel waves themselves
  are UNCHANGED (delegated/parallel steps still wave — only review
  nodes are structurally parallel-forced). A/B-verified: every migrated
  schema/addendum identical to HEAD (`temp_scripts/ab_tool_migration.py`;
  seed↔class drift check `temp_scripts/check_tool_prompts.py`).
  2026-11-06 (c) — THE MODEL-UNREACHABLE CODE FALLBACK: the verdict
  ask rides the SAME provider as the failed step, so a rate limit
  kills it too — immediately (zhipu 1302 is NOT in the transport's
  `_RETRY_CODES`, `llm_agents/_core/transport.py`) — and the ask's
  own failure used to collapse EVERY verdict to `no-action`: the
  throttle was dead code in exactly the rate-limit event it was built
  for (rate-limited nodes still failed). Now `poller.py::
  _handle_lane_failures` classifies the ask's exception in code
  (`repair.is_transient`): transient → the CODE issues the
  `retry-step` verdicts itself (still budget-gated via
  `should_retry`, which keeps reading the STEP's error — a
  non-transient step failure is never smuggled through by a transient
  ask failure; the throttle applies when the ask's error OR the
  step's error is rate-limited AND a retry actually applied), with a
  deterministic `[Auto-retry — decided in code]` resume instruction;
  non-transient → no-action (unchanged; the sweep still reviews it).
  `_handle_lane_failures` now returns whether a supervisor LLM call
  was actually spent (the node item's n_llm_calls stays honest).
  Stub-tested (`test_supervisor_repair.py` (c) section) + live-probed
  (`temp_scripts/probe_supervisor_repair_fallback_live.py` — injects
  a 429 into BOTH the step hop and the verdict ask).
  2026-11-06 (d) — THE SUPERVISOR IS BOUND TO THE SUPER THREAD ID,
  never a segment langgraph thread: `super_run` constructs the
  SupervisorAgent + its writers with `self.super_thread_id`, and every
  keyed artifact (the review rows `record_supervisor_review`, the kit
  scope `SupervisorToolKit`, the reopen/ignore event payloads, the
  sweep) resolves through ``super_thread_id or thread_id``
  (`scope_id`) in poller + watchdog; the lane handler's state read is
  the CHAIN read (`_watched_states`) — the root-thread read used to
  wake the agent with an EMPTY failure manifest over a plan-only tree
  (the live probe's reviews said exactly that). Today the root P
  thread's id IS the chain id, but no binding depends on that
  coincidence; `thread_id` params remain the legacy single-thread
  fallback only. Stub-tested (`test_supervisor_repair.py` — the
  chain-read test asserts `list_super_states` fires, `list_thread_states`
  never does, and the review keys to the chain id).
- `skills/` — the SKILL LAYER (2026-11-02): the run's selected study
  instructions (`llm.ai_prompts` rows) as objects — one `Skill` per
  row, one `SkillBook` per run; the runner renders the LOADED set
  once (`SkillLoader.from_rows` → `SkillBook.render_all`) and
  `SkillBook.items_block` builds the step message's quoted prompts
  block. WHICH rows load is the GRADUAL INCREMENTAL DISCRETIONARY
  PROMPT-LOADING LADDER (2026-11-02, `planning/prompt_loading.py` +
  the plan node): catalog previews (`store.fetch_prompt_catalog` —
  never the texts, plus instruction LENGTHS for the eager budget) →
  pure keyword match (`match_prompt_labels` — bound_labels force-load
  per the ai_prompts DDL, run-scoped `prompt_labels` overrides, item
  labels in the haystack) → past the eager budget (≤4 skills or
  ≤4000 chars) the planner's `load_prompts` VERDICT
  (`plan_json.parse_plan_reply`, plan-JSON contract extension) picks
  the skills the plan works under; the decided set's texts land in
  ThreadState AT THE PLAN NODE as `prompt_texts` (the pass-down) and
  the rest as `prompt_catalog` (prompt_key → one preview line).
  2026-11-07: the HOTPATH CORROBORATION RUNG between keyword match
  and verdict — `planning/kg_prompts.py::HotpathPromptProbe`
  (read-only: trigram-probes the PROMOTED `llm.kg_hotpaths` chains
  similar to the run's needles, resolves them through the TTL-cached
  live graphs, skips stale `entity_hash` chains) — an optional-labeled
  row whose prompt_key names a promoted ns1 chain entity, or whose
  optional labels hit the chains' bilingual entity vocabulary, returns
  the `"hotpath"` verdict and LOADS like a bound force (precedence
  forced > hotpath > matched; capped by `deep_agent_config`
  `prompt_hotpath_rows`, 0 disables; bound rows keep their
  unconditional load, asserted in `temp_scripts/test_prompt_ladder.py`,
  the probe stub-tested by `temp_scripts/test_hotpath_probe.py`, the
  live lane proven by `temp_scripts/probe_hotpath_prompt_lane.py`
  with a synthetic promoted chain in and out);
  2026-11-09: the MATERIALIZED-DATA hints — every step hop's directive
  is KG-searched READ-ONLY (`KnowledgeGraphService.table_hits`, no
  chain log/promotion) and the surfaced analysis tables ride the step
  message as the `[Materialized data — LOAD before computing]` block
  (`StepPromptContext.data_hints` + `base._data_hints`, wired through
  the optional `StepDeps.get_data_hints`; at most `data_hints_rows`
  tables, 0 disables) — so a step loads what the analysis schema
  stores (e.g. the ma-spread suite) instead of recomputing it from
  bars, the run-2af6c08d failure this closes. The same round taught
  sourcing at the standing layer (the step addendum's MATERIALIZED
  ANALYSIS DATA paragraph, seed + constant synced) and put the
  vocabulary the steps actually ask into the KG seeds (排列/间距/
  长短均线/多头排列/空头排列/alignment on the ma-spread family + the
  ns1 ma-spread-guide; mined from run 2af6c08d's OTHER spawns the same
  way: 量价 family 放量/缩量/量能/成交量/背离/量价关系 on
  mov_ave_price_vs_amt + mov_ave_trading_amt, 超买/超卖/相对强弱 on
  mov_ave_rsi, 区间涨跌幅 on mov_ave_momentum, 金叉/死叉/cross on
  detail + _ema, K线形态/candlestick on _ohlc_gaps, bollinger/布林带/
  带宽 on detail — every spawn query in
  `temp_scripts/test_kg_ma_queries.py`'s CASES now finds its store;
  extended to the SIGNAL TIER (macd/ma_cross/cross/金叉/死叉/技术信号/
  crossover tags on analysis_signals.signal_rules + history_signals +
  history_signal_performance — the macd family's macd_cross_up/dn
  cells ARE the 金叉/死叉 events, materialized ~24k rows in
  history_signals);
  asserted by `temp_scripts/test_data_hints.py`
  and the live `temp_scripts/test_kg_ma_queries.py`;
  2026-11-10: the lane REACHED THE SPAWNS — the run-532c6a78 study
  (temp_scripts/study_super_532c6a78*.py: 5.17M input tokens over 168
  requests; spawn children never got the hints block —
  `get_data_hints` was wired in `build_step_deps` only, the step lane
  + review seats) — the shared lookup extracted to
  `langgraph/data_hints.py::data_hints_for` (base delegates; the knob
  lives THERE now) and `SpawnHooks.input_hook` stamps the spawn goal's
  hints as `SubagentRequest.user_addendum` (a generic core slot:
  the addendum text appended after the child's structured-context
  fence; `DeepAgent.run(user_addendum=...)`), so every spawned child —
  sub-sub children through `child_hooks` — rides the same
  load-before-compute block; stub-tested
  `temp_scripts/test_spawn_data_hints.py`. Same round: the KG result
  render's GRADUAL-LOAD TIERS (kg_tools `detail:'lean'|'full'`, lean
  default — 4 head nodes × 80-char descs × 5 columns, view-blocks
  bounded; the study's 14–20K-char dossiers render 3.3–4.4K lean,
  2/13 had hit the 20K tool-result cap); the prompt round (seed +
  constant synced, `apply_study_532c6a78_prompts.py`): the spawn plan
  addendum teaches load-the-[Materialized data]-block-first, the
  subplan pre-planner prefers READINGS over computations, the step
  addendum gains the synthesis-steps-read-don't-re-derive clause
  (the 982K finalize re-studied the whole run) + decode-label-codes-
  ONCE-per-conversation (12 label_dim re-queries in one
  conversation); 2026-11-11 the addendum gains the SANDBOX SCRIPTS —
  ITERATE SMALL, FAIL FAST clause (per-block try/except, read the
  traceback's LAST line, patch with sandbox_edit_file — the run
  e1c366bd rewrite loop) plus the RETRIEVE-SMALL-THEN-AGGREGATE
  clause (one narrow SELECT per fetch filtered on the table's key
  grain, then aggregate/derive in pandas or a follow-up small query —
  a multi-CTE monolith fails as a whole; the same teaching landed in
  the `db_run_query` MCP schema + the server instructions sentence
  that previously said "aggregate … in SQL", and in
  SUBAGENT_SYSTEM_PROMPT's narrow-SQL clause — the subagent system +
  the derived `deep-agent-system` standing row both re-seeded); and the plan session's `add_plan_states` calls pass
  `stamp_state_context=False` — the plan ctx pre-stamp on business
  step rows blocked the hop's own NULL-guarded stamp, so later-batch
  steps' real conversations went invisible on their state rows (run
  532c6a78's 982K finalization read as the plan's 2-request
  conversation; the pre-existing test drift it exposed —
  test_prompt_ladder's recipes-stripped contract vs the new
  scoped_data face — is pre-session, flagged not fixed); the dummy
  LLM's rule table now strips the `[Materialized data]` teaching
  block before matching (the tool-demo rules matched its tool names
  and looped every hints-carrying conversation to the step budget —
  the probe's seats failed since the 2026-11-09 hints round);
  probe_dummy_langgraph green again both variants;
  the same round
  taught the SYNONYM LOOP on every kg_search surface — the tool
  description, the ns0/ns2/merged post-result hints and the no-match
  render all instruct the model to PROPOSE 2-3 SYNONYMS / paraphrases
  (Chinese AND English trader terms, e.g. 间距→spread→乖离,
  排列→alignment) and kg_search them again before listing schemas
  blind (exact-token matching makes vocabulary iteration THE
  refinement mechanism);
  2026-11-08: the KG tools' retrieved TABLE hits carry their exact
  COLUMN names + comments auto-loaded from pg_catalog
  (`llm_agents/kg/columns.py::TableColumnCatalog`, template
  `database/sql_views/llm_agents/kg/columns/table_columns.sql`; the
  three renderers take `columns=`; search enriches its top-3 tables,
  expand/replay theirs) — an LLM reading a kg result can LOAD the
  table without db_describe_table; 2026-11-09 the SAME block also
  carries the table's KEY GRAIN — the PRIMARY KEY + up to 2 secondary
  indexes (template `table_keys.sql`, fail-soft to columns-only) — so
  the model filters on the key columns (usually (code, date)) straight
  from the hint, and the stats_macd tool reads the MATERIALIZED
  stats.*_tech_stats macd_line/signal/hist columns (the same round
  materialized them + ema9; the stale v_*_daily ma255 views rebuilt,
  un-breaking stats_ma/stats_macd);
  2026-11-03: the ladder is DYNAMIC across the WHOLE graph —
  `node_prompt_matches` (label hits on a node's OWN directive, plus
  any study prompt key the directive/goal NAMES — a sub-plan goal
  naming "study prompts ma-spread-guide, pe-guide" auto-loads them in
  that child) runs at every inline hop (a `[Step-matched prompts]`
  block) and every delegated goal (merged into that child's
  pass-down); keyword-matched-and-loaded rows are exempt from further
  discretion (the higher-priority contract); and the catalog-gated
  `load_prompt` tool (`tools/prompt_tools.py`, prompt_type 4 sub_type
  'prompts', per-run raw-text cache, `{{key}}`-rendered) rides EVERY
  conversation — inline steps, spawned children through
  `subagent_hooks(extra_builtin_tools=…)` (recursing to sub-sub
  children), and the degraded execute lane. The planner reads the
  COMPACT data variant (`planner_scoped_data`: item identities +
  metric names, never the per-metric db_keywords recipes or docs
  bodies — steps keep the full data; 71% measured cut). Measured
  driver: an all-scopes-all-skills run's first user message was
  80,920 chars (76 KB data block) and every conversation then
  wholesale-fetched 25 skill rows ≈ 19 KB. THE THIN GENERAL PLAN
  (2026-11-03): the BROAD-RUNS clause keeps the main plan thin —
  scope entry + delegated SUB-PLAN section spawns + final_view — and
  the pickable `general-plan` row (prompt_type 2, seeded in
  `static/ai_prompts/09_plan_prompts.sql`) encodes the same shape;
  each sub-plan goal opens with "First plan your own sub-steps:
  update_plan add_nodes under your spawn, then work and tick them",
  NAMES its study prompt keys (auto-loaded in the child via node
  matching) and its item group — the granular biz planning happens
  INSIDE the sub-plan node on the existing SpawnPlanToolKit (the spawn
  addendum teaches plan-first). Fixed en route: execute_node's
  NameError on `agent_prompts` (closure scoping broke the degraded
  lane). 2026-11-04: the STUDY-SCOPE plan-prompt family — four
  mutually exclusive scope directives seeded in
  `static/ai_prompts/09_plan_prompts.sql` (study-codes /
  study-codes-related / study-market-codes / study-market-whole;
  plan-type rows load when selected and render `{{code}}`/`{{name}}`).
  The runner structurally auto-loads the applicable DEFAULT when the
  pick names none of `planning/planner.py::STUDY_SCOPE_PLAN_PROMPTS` —
  `default_scope_plan_key`: codes named → study-codes, no code tag
  (All Market) → study-market-codes; an explicit family pick
  suppresses the default.
- `langgraph/decision/` — the SYSTEM ONE decision module (2026-11-12,
  migrated from the package root `decision/` into the COMPOSING
  layer): typed harness judgments answered by the resident host's jev
  models over the validated `/v1/systemone` contract (see
  `jev-systemone` — the serving fixes that made it usable:
  per-question forwards, the letter-token read, the CJK candidate
  filter, response validation in `server_cluster/llm`).
  `templates.py` the `DecisionTemplate` ABC + the standing registry —
  `resume_judgment` (supervisor node resumption → the repair lane's
  verdict) and `node_spawn` (the spawn breaker's gate); a NEW judgment
  = one subclass + one `TEMPLATES` entry + one call site, the
  engine's read path never changes. `engine.py` `JevDecisionEngine` —
  fail-soft (`ok=False` on every exit), short read timeout,
  confidence floor; `facts.py` the narrow typed inputs (never a
  transcript). BOTH judgments are noul on purpose (yes/no is the
  model's calibrated strength; lettered choices read position-biased).
  LAYERING: the live call sites sit OUTSIDE langgraph on purpose —
  `core.loop` (the spawn gate) and `supervisor.workers` (the verdict
  ask) hold the duck-typed INJECTED engine and call its
  PRIMITIVE-KWARGS methods (`decide_spawn(...)` / `decide_resume(...)`)
  so they import nothing from langgraph at runtime; the composing
  layer wires `JevDecisionEngine.active()`
  (`langgraph/runner/base.py::_hop_common` +
  `langgraph/super_run/chain.py`), None = the legacy lanes — every
  existing stub test stays deterministic. Consumers: the RepairWorker
  fast path (affirm-only, never a veto) + `_maybe_spawn_repair`'s
  gate. 2026-11-12 (d) — the KG RELEVANCE GATE: `kg_search` /
  `kg_expand` results are judged by the resident jev model BEFORE the
  render enters the conversation. Template `kg_relevance` (one
  `choice` read over the ranked entities + explicit `all` and
  `reject` options → a MULTI-ANSWER: `all` accepts every candidate
  floors-waived, every candidate clearing
  `jev_kg_accept_floor` ships, the comma-join rides
  `JevDecision.action`; reject argmax / nothing clearing → rejected);
  the read carries its OWN ok floor `jev_kg_confidence` (default
  0.35 — measured: real dossiers spread mass across related
  entities, top ~0.2–0.4 with the RIGHT ranking, while noise rejects
  read 0.9+ and anchors 0.80/0.91; the single-pick floor does not
  fit a spread distribution). Engine `decide_kg_relevance(...)`;
  `tools/kg_tools/gate.py::KgJevGate` adapts the injected engine onto
  the service's `judge` callback (`llm_agents/kg` stays independent —
  `service.search_gated`/`expand_gated` return `(text, verdict)`,
  filter the render to the accepted entities BEFORE the column load,
  and never gate a HOTPATH REPLAY — a twice-proven chain is already
  validated); the tool layer mounts the gate through
  `KgToolSet(decision_engine=...)` wired from the runners' engines
  (the spawn hooks thread it to every child too; the supervisor kit +
  CLI stay ungated). REJECTED results send ONLY the synonym-loop
  nudge (the token saving the gate exists for); a disabled knob
  (`jev_kg_gate`, `JEV_KG_GATE=0` kill-switch), a missing engine, an
  unavailable host or a below-floor read ships the UNGATED render —
  the gate can never blank a result it could not read. Stub-tested
  `temp_scripts/test_kg_jev_gate.py` (17 checks), live-probed
  `temp_scripts/probe_kg_gate_live.py` (anchors + real gated flows;
  a 布林带 query whose traversal returns spreads-family tables is
  the intended REJECT — the nudge teaches 布林带→带宽→乖离/spread,
  the KG synonym loop).
- `planning/` — the living-plan domain: plan-JSON contract with
  propose→validate→correct repair rounds (`planning/planner.py`),
  `PlanAuthority` permission model (`planning/authorities.py` — the
  model-facing tools over an authority live in `tools/plan_tools/`;
  permission stays enforced HERE in code), PG persistence
  (`planning/store.py` → `llm.deep_agent_states/contexts/identities`).
  2026-11-07 (b) — the OOP SUBPACKAGE SPLIT: the five big flat
  modules became same-named SUBPACKAGES whose roots re-export the
  whole prior surface (zero caller churn — every
  `planning.<mod>` import line keeps resolving, the
  `sql_writes/.../planning/store/*.sql` mirror path too):
  `store/` (models / base `._insert_context` the ONE contexts write /
  prompts `AGENT_PROMPT_*` + hotpath evidence / states lifecycle /
  outputs+finalize / chain reads-purges-settle / identities, composed
  into `class PlanStore(<mixins>)`), `plan_json/` (contract /
  prompts / parse / review_wave), `planner/` (template_args /
  assembler / planner), `prompt_loading/` (catalog / ladder /
  scoped_data), `session/` (standing / pass_down / session). Legacy
  removed in the same round: the pre-three-graph `final_view`
  machinery (`FINAL_VIEW_NODE` / `ORDERED_NODES` /
  `strip_closing_wave` / templates' `strip_legacy_closing` — DB
  verified zero legacy type-plan rows first), the dead
  `plan_reply_steps` / `catalog_block` / `identity_from_scope` /
  `ThreadPlan.state_by_ordinal` / `PlanWriterFactory` / the
  `search_thread_context` delegate, and the spent broken
  temp_scripts (imports of the long-gone `PLAN_SYSTEM_PROMPT`,
  `eager_load`/`loaded_keys`); `thread_search.py`'s contexts-lane
  render KeyError (`row["self._thread_id"]` literal-string index)
  fixed. The prompts stay the fail-soft fallbacks of their DB rows
  (byte-sync still asserted by `temp_scripts/check_agent_prompts_
  sync.py`); A/B harness: `temp_scripts/verify_planning_refactor.py`
  (surface snapshot + every-import-resolves; 12 stub tests + the
  dummy-LLM three-graph probe green).
  2026-11-03: the plan's GOAL SEMANTICS — every node serves ONE goal,
  goals NEST BY PATH (a main-path node the GRANDER per-aspect goal,
  spawn / sub-plan nodes smaller goals cut from it), and a completed
  tick marks that goal FINISHED — taught across the planner system
  prompt (`plan_json.plan_system_prompt`), the step addendum (the
  reply IS the goal's settlement), the spawn plan addendum, and the
  tick / spawn-update tool descriptions (seeds 01/09/10 refreshed +
  applied by `temp_scripts/apply_goal_semantics_round.py`).
  2026-11-03: the BATCHED PROPOSAL PROTOCOL — the plan proposal no
  longer asks for the whole plan in one giant reply (the measured
  failure mode: a 80 KB message + a full-plan generation blows the
  provider's 120s read timeout ×3 and the run degrades to no-plan).
  The reply contract carries a `more` flag
  (`plan_json.parse_plan_reply`): the planner's first (init) call
  proposes the first few steps, every accepted batch is followed by
  ONE small follow-up turn (`batch_followup_message` — nodes-so-far +
  next-batch ask) until `more` closes or
  `limits.plan_proposal_batches` (default 6, UI-editable) is spent.
  Each validated batch persists NOW (`runner._propose_and_persist_plan`:
  `create_thread` on the first, `store.add_plan_states` on the rest,
  `store.replace_plan_payload` lands the complete payload at stream
  end) — the UI graph fills in DURING planning; a mid-stream failure
  keeps the persisted batches as a PARTIAL plan (payload stamped
  `partial`) instead of degrading to no-plan. Stub-tested
  (`temp_scripts/test_batched_planner.py`).
  2026-11-03: THE REVIEW WAVE (SINCE 2026-11-06 THE RESULT GRAPH'S
  FIXED FOUR SEATS — see the langgraph/ bullet — no longer the plan's
  closing section): `plan_json.REVIEW_WAVE_NODES` —
  `return_estimate`, `risk_analysis`, `optimistic_review`,
  `pessimistic_review` — FOUR INDEPENDENT review agents executing as
  ONE concurrent wave of fresh step conversations in the RESULT thread,
  each reading the custom graph's findings AND its graph-output
  synthesis through its own role lens (expected
  return; risks by probability×impact with triggers; steelmanned bull
  case; steelmanned bear case); the seats come from the SEEDED
  type-result template row (DB-editable goals, fail-soft to the
  constants) and are NEVER delegated (a spawn inherits
  no findings, so it cannot review): `_review_fixup` stays as defensive
  parse normalization, `strip_closing_wave` drops the legacy closing
  section from saved-template loads, and the planner prompt/plan lane
  no longer teach, nudge or floor them (all three removed 2026-11-06 —
  the RESULT graph's existence is the structural guarantee now). A
  failed reviewer settles its row but
  does NOT end the lane (the output node notes it; the watchdog
  still diagnoses it). Seeds 01/09 refreshed + applied by
  `temp_scripts/apply_review_wave_prompts.py`; stub-tested
  (`temp_scripts/test_review_wave.py`).
- `agents/` — the AGENT LAYER (2026-11-06): `NodeAgent`
  (`agents/base.py`) — ONE agent == ONE graph node: identity
  (thread id / node name / state row) + the node's ONE chained LLM
  context (injected writer factory + request chain; langgraph only
  under TYPE_CHECKING). `PlanAgent` (the planning lifecycle;
  `planning/session.py` keeps the pure engine), `SupervisorAgent`
  (the repair-lane session — its waking interventions now run under
  their OWN begun writer + bound chain, first-class context rows),
  `StepAgent` (`agents/step_agent.py` + `StepDeps` — the inline hop's
  conversation with runner-injected collaborators; `resume()` is the
  node==agent payoff: the retry verdict carries `Verdict(action,
  instruction)` (`supervisor/repair.py`) and the re-picked hop runs
  the agent in resume mode — a `[Resume — supervisor instruction]`
  block rides the step message with the instruction, the prior
  attempt's error and the prior chain ref via search_thread_context;
  the map rides ThreadState `resume_instructions`, written by the
  repair gate, consumed + carried forward per hop).
- `planning/session.py` — the PLAN ENGINE (2026-11-06):
  planning as a standalone agent OUTSIDE the graph — `run_plan_session`
  (awaited by run_thread BEFORE ainvoke; no plan node, no checkpoint,
  no state round-trip) owns the batched-proposal engine
  (`propose_and_persist_plan`, verbatim from the former runner block),
  the prompt-loading ladder helpers (`fetch_prompt_catalog_ladder` /
  `load_decided_prompts` / `PromptBudget`), the spawn pass-down packager
  (`spawn_biz_context`) and `standing_system` (re-exported by
  langgraph.prompts). Deep-but-not-complex; spawning sub-agents from a
  future engine needs no structural change.
- `langgraph/` — LangGraph StateGraph orchestration, since 2026-11-06
  the THREE-GRAPH CHAIN (`langgraph/super_run.py::SuperGraphRunner` /
  `run_thread` — the orchestrator; `langgraph/runner.py` keeps the
  builders): ONE RUN = THREE chained graphs, ONE THREAD EACH, chained
  by `llm.deep_agent_states.super_thread_id` (= the root plan thread's
  own id, stamped on every row of P/C/R; legacy single-thread runs read
  as their own chain via `COALESCE(super_thread_id, thread_id)`;
  `store.fetch_chain_threads` / `list_super_states` / `purge_super_
  thread` are the chain reads):
  1. PLAN graph (thread P, type 0) — `build_plan_graph`: the PlanAgent
     session as the `plan` node + the graph's FINAL OUTPUT NODE (lands
     the complete plan payload over the first batch's partial row).
  2. CUSTOM graph (thread C, type 1) — `build_execution_graph` over
     the plan's BUSINESS steps (`entry router (plan_ok) → step* /
     compact → finalize → output`, plus the degraded execute lane in P
     when planning failed — no C minted, always-answer unchanged; R
     still runs — see 3). Its output node = the LLM findings synthesis
     (today's final_view role) persisted as the kind `graph-output`
     context (agent_context_ kind 6) — THE HANDOFF.
  3. RESULT graph (thread R, type 2) — `build_result_graph` over
     the SEEDED type-result template row (`default-result-wave` in
     `llm.deep_agent_langgraph_templates` — DB-editable review goals;
     fail-soft to `plan_json.review_wave_steps`): since 2026-11-07 a
     REAL LangGraph — ONE NODE PER SAVED SEAT (`langgraph/runner/
     review_lane.py::ResultGraphBuilder`: `START → four review nodes in
     ONE parallel superstep → review_join → finalize → output`), the
     graph literally as saved from the langgraph template; each seat's
     body is the step lane's inline hop (`StepAgent` over the shared
     `base.build_step_deps` collaborators, `step-<state_id>` items so
     the canvas joins unchanged), seats report only through the
     `ResultGraphState.review_results` reducer channel (parallel nodes
     may not write the plain keys), and `review_join` (LangGraph's
     fan-in barrier) folds the outcomes in ORDINAL order, picks the
     answer (highest-ordinal completed seat) and runs THE REPAIR
     WINDOW (retry verdicts route BACK to the failed seat node via
     `resume_instructions` + `StepAgent.resume`, bounded by
     `REVIEW_JOIN_MAX_PASSES` + the poller's per-node budget; ignore/
     no-action proceed — reviewers never end the lane). Findings seeded
     with the execution lane's synthesis as line 0; output node = the
     final answer persisted as kind final-result. R ALWAYS RUNS (2026-
     11-07): full chain after C AND degraded no-plan runs (P → R — the
     reviews read the degraded lane's own answer; the chain is TWO
     segments, the service labels them plan + result). FOUNDATION FIX
     en route (2026-11-07): `plan_ok` was DECLARED in ThreadState — the
     orchestrator passes it at INPUT and the START routers read it, but
     it was never a declared key and langgraph's branch reader delivers
     DECLARED channels only, so `route_entry` only ever met its
     default-True (the degraded execute lane silently never routed) and
     `route_result_entry` met default-False (the wave skipped); the
     declaration restores both to their documented behavior. Related
     same-day fix: `core/data_definitions/subagent_request.py` carries
     BOTH `SubagentRequest` and `SubagentRun` again (the one-file-per-
     dataclass split left mutually-referential names in two modules;
     pydantic resolves each dataclass's fields against its own module's
     globals, so the split deferred every `AgentResult` schema build and
     NO agent could answer — `subagent_run.py` stays as the re-export
     shim).
  EVERY graph has a FINAL OUTPUT NODE (`agents/output_agent.py::
  OutputAgent` — one directive-carried synthesis conversation, stats/kg/
  prompt tools, no plan kit, no spawns; 2026-11-11 the no-spawns claim is
  now ENFORCED — both output seats and the degraded execute lane
  construct their agent with `allow_subagents=False` so the
  spawn/collect schemas stop mounting there: the output node parents no
  child state and the degraded lane has no spawn hooks, so their spawns
  ran unregistered) — each graph's output is what
  the next graph reads. 2026-11-07: the two output charters are
  DB-EDITABLE type-3 rows (`deep-agent-custom-output-directive` /
  `deep-agent-result-output-directive`, seeded in
  `static/ai_prompts/01_deep_agent_prompts.sql`), each text opening
  with the OUTPUT NODE's place in the three-graph langgraph chain
  (what executes before it, what its document persists as, which
  result-graph seats read it); the runner resolves
  `agent_prompts.get(KEY) or GraphOutputSpec.directive` at the output
  node (the constants in `langgraph/prompts.py` stay as the fail-soft
  fallbacks; `temp_scripts/check_agent_prompts_sync.py` asserts the
  seed ↔ constant byte-sync for ALL 15 internal rows — the same round
  migrated the subplan system, the plan repair / batch-followup
  message frames, the compact system and the supervisor intervention
  frame into rows too, and the plan prompts now teach CUSTOM-graph-only
  planning explicitly: the node-tag vocabulary no longer lists
  `final_view`). ONE sandbox session, ONE wall-clock budget,
  ONE supervisor session (repair channel + chain-scoped poller/watchdog
  via `super_thread_id`) shared across all three; `settle_thread` is
  chain-aware (stop settles every thread). The review wave LEFT the
  plan contract entirely (plan_system_prompt teaches the business
  middle; no closing nudge — removed from planner.py; no floor —
  removed from session.py; `strip_closing_wave` drops the legacy
  closing section from saved-template loads; `_review_fixup` stays as
  defensive parse normalization). The old single-graph `result` node is
  RETIRED (the tag lives on as the final-result context row's node
  key). 2026-11-06: the
  CHECKPOINTER IS GONE — with planning pre-graph and the supervisor
  beside the graph nothing ever reads a LangGraph checkpoint (runs
  always start fresh; the P2 resume item would re-add a saver
  deliberately), every graph compiles stateless, the
  langgraph.* checkpoint tables are purged
  (`temp_scripts/purge_langgraph_checkpoints.py`; tables kept — the
  UI's thread-delete and the 09 prune still reference them
  harmlessly), `entry_checkpoint_id` stamps read the ephemeral
  superstep id or None, and the Thread Graph's ckpt chips stop
  appearing on new threads (index.tsx renders them only when
  present); per-hop max_steps clamped to
  `max(4, min(run_max_steps, 20))`; recursion_limit 160.
  2026-11-02: the SUPERVISOR (`supervisor/` — its OWN package since 2026-11-05, OUTSIDE the
  langgraph package; 2026-11-11 REFACTORED TO THE EVENT-QUEUE PICKER —
  the layering is now event_queue.py (the BOTTOM: `EventKind`
  node_failed/failure_detected/run_settled, `FailureRef`/
  `SupervisorEvent` typed payloads, `SupervisorEventQueue` — a bounded
  coalescing tray, same-kind offers merge with state_id dedupe and
  NODE_FAILED events never merge, drop-oldest never drops a lane
  window; `RepairWindow` — the per-window future the lane's
  `await_verdicts` waits) → repair.py (the policy + the lane facade:
  the classifiers/Action parser/`should_retry` gate unchanged, the
  `RepairChannel` now the WINDOW REGISTRY + supervision LEDGER over
  the queue — the lane's two calls `signal_failures`/`await_verdicts`
  keep their shapes so the step lane and review join are untouched; a
  superseding signal resolves the stale window {}) → classify.py (the
  pure picker classifier: `EventClass` REPAIR/REVIEW/SWEEP +
  `CLASS_PRIORITY` — the paused lane's repair jumps the belt reviews
  and the terminal sweep) → workers.py (the WORKER SUB-AGENTS the
  picker launches from the `WORKER_FACTORIES` registry: RepairWorker —
  the former `_handle_lane_failures` decision body incl. the
  model-unreachable code fallback and the throttle; ObserveWorker —
  the belt's observe review; SweepWorker — the final sweep; a NEW
  supervision capability = one EventClass + one subclass + one
  registry entry, the picker loop never changes) → poller.py (the
  PICKER: `SupervisorSession.run` drains the queue, drops superseded
  window events, sorts by class priority and dispatches each event's
  worker under the agent-context lifecycle — a first-class context
  row; the interval belt scan produces FAILURE_DETECTED events for
  unsignalled root-path failures; the runner's `repair.close()` offers
  the terminal RUN_SETTLED event whose dispatch IS the final sweep)) —
  an INDEPENDENT
  per-run agent beside the graph, NOT a LangGraph node, sharing no
  path or data lane with it (the DeepSeek Super-agent
  one-router-many-workers pattern: deliver events not prompts; code
  filters first, the model judges only what passes). IDLE on healthy
  runs — zero LLM, zero context; the trigger is the pure filter
  `root_path_failures` over the settled node states (a failed
  top-level step or the failed planning row; spawn failures stay
  local, user stops are not defects). An intervention is ONE bounded
  call in the watchdog's own fresh context over the LEAN failure
  context (run goal + plan tree as title/status lines + each failed
  node's directive/error/note — never the findings bridge or
  transcripts): it diagnoses the error and judges whether the
  plan/graph topology needs an update — OBSERVE MODE, the
  recommendation recorded as ONE context row of kind supervisor-review
  (agent_context_kind 5, `planning/store.py::record_supervisor_review`)
  keyed to the watchdog's ONE chained context, never applied, no
  veto, fail-soft (a failed call settles the item failed and changes
  nothing). The UI's Thread Graph (since 2026-11-06 the CHAIN canvas —
  `graphModel.ts::buildThreadGraphModel` renders every segment of the
  super chain side by side, joined by the DASHED chain-styled HANDOFF
  edge between consecutive graphs' output/entry nodes across the wider
  X_CHAIN_GAP (2026-11-08: spacing constants retuned for the fixed-px
  labels vs the auto-fit — taller rows, wider terminal advance, spawn
  goals labeled below), each
  segment captioned plan/custom/result; `ai-thread.service.ts` merges
  the chain into one detail with per-segment `s<k>:` node-name
  namespaces; the run history groups by COALESCE(super_thread_id,
  thread_id) and delete purges the whole chain) draws the supervisor as
  ONE
  free-floating circle above the lane over the root segment's terminal
  with NO edge to the
  graph (`graphModel.ts`), shows its state by default beneath the
  canvas when no node is clicked (`ThreadGraph/index.tsx::
  effectiveNodeId`), and the canvas sublabel carries the verdict /
  idle state. Its system prompt is the internal prompt row
  `deep-agent-supervisor-system` (prompt_type 3, seeded by
  `temp_scripts/apply_deep_agent_prompts.py`).

Delegation: sync `spawn_subagent` + background spawns with an explicit
`collect_subagents`, an answer gate for uncollected spawns, parent-owned
supervision hooks (`core/delegation.py`, `langgraph/hooks.py`).
2026-11-06: the PROVIDER IDENTITY (model / chat endpoint / API key) is
UI-editable in `static.secret_config` (DDL
`database/sql/static/secrets/`, Config → Secrets tab, writes via
`database/sql_writes/data_viz/api/secret_config/*.sql`; the key is
AES-256-GCM
encrypted at rest, `LLM_SECRET_KEY` in database/.env) and resolves AT
AGENT START — `deep_agents/llm_config.py::load_llm_config()` (one
fail-soft DB read per run, never cached past it) feeds a FRESH
`ZhipuChatProvider(api_key, api_url)` per run in `server.py::_run_one`
and the CLI; the lifespan `_provider` is now only the no-row fallback,
an explicit `RunRequest.model` still wins over the row. 2026-11-06 (b):
the registry is the GENERIC credential store (secret_type SMALLINT
domain `secret_type`: 0 llm | 1 web_search — the provider domain is
selected by the type, the audit pattern) and the ROOT `.ENV WAS
DELETED`: its keys migrated into the registry and EVERY
`BaseLlmProvider` resolves its key via
`_core/secret_config.py::get_secret` after the env chain (search
providers declare `SECRET_TYPE = "web_search"`; TTL-cached, fail-soft;
`downloads.macro.zhihu.news.get_api_key` falls back the same way).
2026-11-07: the PROVIDER LABEL GENERALIZED + the DUMMY LLM —
`llm_config.py::build_chat_provider(label, api_key=, api_url=,
max_output_tokens=)` is the ONE label → class mapping (zhipu | dummy,
unknown labels warn + degrade to zhipu; lazy imports keep the module
stdlib-only at import), `load_llm_config` now returns the active row's
OWN `ext_llm_provider` label, and an explicit
`RunRequest.provider` / CLI `--provider` wins over the row the same way
`model` does (a request naming a DIFFERENT provider than the row runs
that provider on its own defaults — the row's key/endpoint/model do not
leak across). The DUMMY LANGGRAPH DOUBLE (`test/dummy_langgraph/` at
the PROJECT ROOT — migrated out of `llm_agents/test/` 2026-11-10,
RENAMED from `test/dummy_langgraph/` 2026-11-11 when its serving logic
finished migrating to the server cluster: the HTTP envelope moved into
the host's `DummyChatBackend`, and `build_chat_provider("dummy")`
dropped its in-process lane — the dummy label is HTTP-sim-only now,
defaulting to the resident host endpoint; the `test` package, the
harness never imports it) is the
offline test double — 2026-11-12 (e) REBUILT SIMPLE (the legacy
rules/dynamics/infra package below is DEMISED; do not reference it,
the feature files and the two thin servers are the whole package now):
2026-11-12 (f) THE PACKAGE SPLIT IN TWO — the request→response ENGINE
migrated to the parallel package `test/dummy_llm/` (on matched what
request, return what response): `_gherkin.py` (the shared parser,
re-imported back), `DummyLlmProvider` (the renamed
`DummyLanggraphProvider` — the old name kept as an alias), the
chars/4+fixed-floors `estimate_usage` the host envelope reports, and
the chat
dataset `features/01_summary_chain.feature`. `test/dummy_langgraph/`
KEEPS ONLY THE CUCUMBER ASSERTION DATASETS for kg and jev:
`features/02_decisions.feature` + `jev.py` (the decision book, the
stub engine, the live checker), `features/03_tools.feature` + `mcp.py`
(the kg/db tool-result seam); its `provider.py` is a shim re-exporting
the engine + a `FEATURES_DIR` pointing at its own features (the whole
historical import surface re-exported from `__init__`). Live
consumers updated: `build_chat_provider("dummy")` builds
`DummyLlmProvider`, the host's `DummyChatBackend` imports the engine
from `test.dummy_llm` (the formerly-broken `estimate_usage` import now
resolves). Contract suite `temp_scripts/test_dummy_langgraph.py`
(26 checks) + the split A/B `temp_scripts/check_dummy_llm_split.py`
(26 checks) green; the resident host was recycled so dummy-1 serves
the new dataset. 2026-11-12 (g) THE TOKEN CONSUMPTION API — the dummy
reports consumption end to end: `estimate_usage` counts SCRIPT-AWARE (2
CJK chars = 1 token, 2 tokens per space-split English word) + FIXED
floors (`FIXED_PROMPT_TOKENS` 128 / `FIXED_COMPLETION_TOKENS` 64,
deterministic, never zero); the in-process
`DummyLlmProvider.chat_complete` audits every call (and every injected
flake) fail-soft through the transport's `record_request` seam —
provider `dummy`, endpoint `AUDIT_URL` `dummy://in-process/chat/
completions`, envelope-shaped response so `llm_requests` + the
cache-aware `llm_usage` view + the Thread Graph token tail render the
in-process lane the same face as the HTTP-sim — and exposes
`usage()` (the cumulative served totals); the host lane's
`ControlResponse` (answered by BOTH `POST /control` and `GET /stats`)
carries `total_prompt_tokens` / `total_completion_tokens` /
`total_tokens`, accumulated per served envelope, cleared by
`reset`. Offline probe `temp_scripts/probe_dummy_token_usage.py`
(9 checks).
2026-11-13 THE ROUTER DATASET EXPANSION — `test/dummy_llm/features/
00_router_chain.feature` (the filename sorts BEFORE 01_summary_chain
so its scenarios load before that file's catch-alls): the System One
router's three E2E walks on the REAL `run_thread` chain (each walk =
own goal + own scripted decision engine + own scripted sandbox seam,
purged after) — SPAWN (the routed [step_3] proposes sandbox script
writing; the router spawns; the scripting child writes → runs → hands
the evidence back; the parent folds; the parent's proposed calls
never execute), JOIN (a spawned role=scripting child's sandbox_run
fails; its edit proposal gets the join verdict → the nudge; the child
hands back HANDBACK), PROBE (a spawned scripting child dies on the
armed flake; the parent spawns an AUTHORIZED investigator which
reports the root cause). THE MATCH-ORDER DISCIPLINE the file's header
teaches: the router-spawned children's conversations EMBED the
parent's markers (the scripting handoff carries "Run goal:
…[step_3]…", the probe handoff embeds the failed goal), so the CHILD
scenarios (keyed on the child-only markers "Scripting handoff" /
"[scripting child]" / "[failing child]" / "Failure probe") are
ordered FIRST — a parent turn never carries those (spawn arguments
ride tool_calls, not message content) while a child turn may carry
the parent's [step_N]; the three walks share the dataset and
discriminate their plans by GOAL markers (路由给子节点 / 回交父节点 /
调查失败原因 → three plan scenarios whose step-3 directives 证据整理 /
回交 / 失败调查 guard the parent scenarios — all three walks' routed
step renders [step_3]). The probe
`temp_scripts/probe_dummy_langgraph_router.py` asserts per walk: the
scenario trace, exactly ONE router read of the walk's kind, the
sandbox-call shape (the parent's intercepted calls never execute; the
nudged-away edit never runs), the plan-tree child rows (read DIRECTLY
by parent_state_id — `list_super_states` returns top-level rows only,
children excluded by design), the flake count (the flake serve is
never tagged in scenarios_used — assert `provider.n_flakes`), the
three-thread chain + four review seats + final answer; purges after.
`temp_scripts/test_dummy_langgraph.py` walks EVERY chat feature file
mechanically (00 + 01) → 50 checks incl. the 12 decision scenarios
(stub + live onejev argmax). The GHERKIN FEATURE FILES under `test/dummy_langgraph/features/` are
the WHOLE scenario dataset (canonical cucumber split — Given = the
input the conversation carries, When = the discriminating trigger,
Then = the reply body or the expected choice; one Scenario = one turn,
its @tag the fixture name, file order the match order; a strict
minimal parser `_gherkin.py` → pydantic specs, load-time validated);
`DummyLanggraphProvider.chat_complete` matches the request against
the feature predicates (`system contains "..."` / `conversation
contains "..."` / `tools are declared`) and returns the Then DocString
(the plan scenario = the plan JSON the REAL `parse_plan_reply`
accepts; first match wins; a miss raises `ReplyNotFound`) — the REAL
`run_thread` chain walks against it (full + degraded variants,
`temp_scripts/probe_dummy_langgraph.py`, purged after); and
`features/02_decisions.feature` is the DECISION dataset — for the
SAME typed facts the feature asserts the REAL onejev model's RAW
argmax choice at its floor (`DecisionChoiceChecker`, measured
expectations: the throttle-class retry reads YES 0.63, plain
operational reads NO ~0.55 — the affirm-only fast path's reason),
served offline by `StubJevDecisionEngine` (records every read). Unit
suite `temp_scripts/test_dummy_langgraph.py` (26 checks). The
superseded legacy description:
`DummyLanggraphProvider.chat_complete` satisfies the
harness's ONE provider contract (`chat_complete(messages, *, model=,
tools=, temperature=, max_tokens=)`) from CANNED replies — zero key,
zero network, zero spend, deterministic — via `DummyReplyBook`'s rule
catalog of DIFF query-text → DIFF reply mappings (14 domain families:
trend/volume/momentum/bollinger/options/margin/crowdedness/signal/
return/risk/bull/bear/synthesis/overview, each reply self-labelled
`DUMMY FINDING (rule=<name>)`), planning-shape detection (a
`plan_system_prompt`/`subplan_system_prompt` system text → a plan JSON
the REAL `parse_plan_reply` accepts, evaluated BEFORE the rule table so
goal keywords cannot hijack planning), shape fallbacks (tools declared
→ finding; else → answer), the `TOOL:name {json}` query directive, a
per-call `script=` mode (str | ReplySpec | dict | Exception;
`script_exhausted="error"` for strict scenarios) and `fail_matching=`
injected failures. 2026-11-11: the DYNAMICS LAYER —
`DummyLanggraphProvider(dynamics=True)` / `DummyReplyBook(dynamics=True)`
(opt-in; OFF = byte-identical old behavior, the standing probes never
see it) — a conversation-STATE-aware rule pass (`DYNAMIC_RULES`,
evaluated after the planning/TOOL phases, before the rule table; a
`ConversationState` ledger maps each tool result to its call via the
call ids) that walks MULTI-TURN tool lifecycles deterministically:
`update_plan` sub-node → `load_prompt` skill loading →
`spawn_subagent`(background) → `collect_subagents` → fold the spawn's
finding into the answer (the spawned child settles on its
`dummy-dynamics-fork-sub` goal marker); and the sandbox
write→run→fail→edit→fail loop (`scripts/dummy_study.py` with two
planted ambiguous-column bugs — the parent's `sandbox_edit_file` fixes
the first, the SECOND traceback failure triggers the loop's
fresh-context repair spawn, whose child patches the second over the
SHARED sandbox session, re-runs green and hands `REPAIR EVIDENCE`
back; the parent folds it in). Scenario markers keep the walks
disjoint (`dummy-dynamics-repair` / `dummy-dynamics-fork-root`).
Unit-walked by `temp_scripts/test_dummy_langgraph_dynamics.py` (17 checks —
incl. the OFF contract), end-to-end through the REAL loop by
`temp_scripts/probe_dummy_langgraph_sandbox_dynamics.py` (12 checks — the
repair spawn registered foreground + the background spawn gathered, 3
sandbox executions, both edits landed in the shared file). Same day
the SCENARIO CATALOG — `test/dummy_langgraph/scenarios/` (mounted via
`DummyLanggraphProvider(scenarios=["<key>", ...])` / `DummyReplyBook(
scenarios=...)`, OFF = byte-identical old behavior): one
`ScenarioChain` per likely-troubled LLM process, each an ordered
mistake ladder whose turns read the conversation's tool ledger
(`ScenarioState`, defined beside `ConversationState` in replies.py)
and arm only on their own `dummy-scenario-*` marker —
`sql-buggy-thrice` (the requested chain: buggy SQL ×3 — syntax error →
hallucinated column → ambiguous ref — then the corrected query and the
finding, ONE conversation; the DB lane has no repair spawn),
`sandbox-py-ladder` (a 3-bug script crossing the repair spawn, whose
child patches the remaining bugs over the shared session and hands the
evidence back), `plan-json-repair` (planning-phase: broken plan JSON
twice, riding the REAL parse_plan_reply repair rounds), `tool-args-json`
(invalid tool-call argument JSON — needs `ToolCallSpec.raw_arguments`),
`label-code-raw` (raw SMALLINT codes → the label_dim decode join),
`pk-grain-missing` (missing (code, date) PK filter → the row cap),
`hallucinated-table` (invented relation → kg_search → corrected); the
matching fake tool seams ship in the package (`_fakes.py`:
`ScenarioDbMcp` dispatches on the `/* dummy-scenario:<chain>:<turn> */`
comment each canned SQL carries, `ScenarioSandbox` returns the
traceback of the first remaining planted bug); the sandbox chain's
parent fold taught chains the `arm=` override (the spawn's injection
message carries the child's evidence but NOT the run-goal marker —
without it the standing tool_demo rules looped the fold conversation to
the step budget) and `requires_tools=` (a tool-less conversation never
arms a db-lane ladder). 2026-11-11 (b) THE DEFINITIONS MIGRATED TO
TEXT — cucumber-like feature files (`test/dummy_langgraph/scenarios/features/
*.feature`, `Feature / Background / Scenario / Step / When / Then` with
```-fenced payloads; the 13 per-scenario Python modules deleted): the
catalog declares WHAT it walks, the machinery owns HOW — `_parser.py`
(strict grammar → `FeatureSpec` pydantic specs; load errors raise at
import), `_builder.py` the ONE shared-logic layer (the When-atom
vocabulary — counts/marker-hits/repair-frames/handoff anchors; the
action builders — call with Sql/Query/Arguments/Raw-arguments payloads,
run/write/patch, reply, reply plan through the real `PlanStepSpec`
contract; the `Ladder: sandbox-fork` EXPANDER — ONE generator produces
the whole write→run→edit→spawn→child-patch→evidence→fold walk for ANY
K-bug script, replacing the per-module turn code; the seam-table
collector — every feature's `Seam returns` fenced text, db calls keyed
by their SQL's scenario comment, kg calls by exact query text),
`_fakes.py` now only the two GENERIC seams with fixtures INJECTED
(`build_db_seam(FEATURES)` / `build_sandbox(key)`). Same day the catalog was POPULATED FROM THE
RECORDED FAILURE HISTORY (the read-only miners
`temp_scripts/study_dummy_failure_history.py` — failed
`deep_agent_states` settles + `is_failed` `llm_requests` rows — and
`temp_scripts/study_dummy_failure_payloads.py` — error fingerprints
inside the request payloads): six chains whose error texts are the
RECORDED ones verbatim — `sql-group-by` (the recurring
`psycopg.errors.GroupingError: column "latest.r" must appear in the
GROUP BY clause`), `sql-alias-swap` (`column "l.r" does not exist` —
the column exists, the alias is swapped), `sandbox-data-bugs`
(date-vs-str `TypeError` → empty-frame `IndexError`, across the repair
spawn), `sandbox-missing-script` (the phantom run — `no such file in
the session directory`; the fake raises it run-before-write), 
`kg-no-match-synonyms` (the REAL rendering.py no-match render → the
taught synonym loop), `step-budget-burn` (the decode-every-turn
run-532c6a78 waste shape until the loop's step cap raises the recorded
`hit the N-step limit` error — no success end, by design);
`ScenarioSandbox` grew ctor-args ladders (tracebacks/green_stdout) +
script-frame-aware bug detection. The study's infra classes (429
1302/1701/1113, ReadTimeout, 1301 sensitive-content, 1211
model-not-exists) are PROVIDER-level — injected via the server's
`/control` or `fail_matching` + a custom `fail_message`, never via
reply chains. Unit-walked + end-to-end by
`temp_scripts/test_dummy_langgraph_scenarios.py` (123 checks — book-level walks
for every chain read through the FEATURES/SCENARIOS accessors, the
plan chain judged by the REAL parser, E2E runs of the sql/args/two
sandbox-fork/noscript/burn chains through the REAL loop; the fixtures
asserted from the loaded features, never re-declared). 2026-11-11 (c)
the WHOLE-PROCESS trio + the grammar's second arm: `whole-process-plan`
(the PLANNING-phase two-batch proposal whose NODE TAGS hand every
mounted scenario its step — titles stay CLEAN because the findings
bridge quotes titles into every later conversation; the delegated
spawn goal carries the kg marker, riding only the child's conversation;
every turn gated on the MAIN planner's system text so the spawn
children's sub-planners answer generically), `transient-flake` (the
step whose first attempt dies on a provider read timeout — the
builder's new `Then fail` action, the error text transient-classified
by the REAL `supervisor.repair.is_transient` — and completes on the
supervisor-resumed re-run), `flake-verdict` (the repair lane's verdict
ask, gated on the supervisor system + the `[Repair protocol]` block +
the `[flake_state]` tree tag — the observe/sweep asks carry neither —
returning the `Action: retry-step` verdict the REAL
`repair.parse_action` gates the retry on). The new grammar pieces: the
When atoms `system contains "<mark>"` / `query contains "<mark>"`
(CASE-FOLDED — the supervisor row announces "SUPERVISOR" uppercase)
and `resume active`, the `Arm: node <tag>` header (a chain also arms
on a graph node tag — the tag rides its own step's directive line and
the supervisor's plan tree, NEVER the findings bridge, whose lines are
title-only; the marker stays the doc/test handle), `reply plan` now
honors `load_prompts` and flattens the real contract's
`{"delegate": {"goal": ...}}` into `PlanStepSpec`. The catalog now
drives the WHOLE three-graph chain offline:
`temp_scripts/probe_dummy_langgraph_whole_process.py` mounts the trio beside
the db/sandbox/kg ladders, injects the text-built seams through
`run_thread(..., mcp=build_db_seam(FEATURES), sandbox=build_sandbox(
"sandbox-py-ladder"))` (the NEW `sandbox=` seam param on
run_thread/SuperGraphRunner — a pre-filled sandbox cell, the degraded
lane's direct `Sandbox()` mint routed through `run_sandbox` so the
injection reaches every lane), and asserts the walk at the provider
seam + the settled chain: plan batches more true→false, the db ladder,
the sandbox spawn (handoff + fold), the delegated kg child, fail →
retry-step verdict → resumed re-read → completed, four review seats,
three threads, EMPTY error scan, purge on clean. Probing it surfaced
and fixed THREE harness bugs: (1) the step lane's instructed-resume
was DEAD — the repair window wrote verdict entries into a per-hop dict
that reached the state only via the node-return update whose
resume-map had already been snapshotted before the window ran (and the
same-invocation re-pick read the stale state) — now a node-scope
`resume_map` feeds `_inline_hop` and the carry-forward is assembled
AFTER the window (`runner/step_lane.py`); (2) `StepPromptContext.
resume_block` existed end-to-end but `build_message` dropped it — the
`[Resume — supervisor instruction]` block never reached the model
(`langgraph/prompts.py` renders it above the directive);
(3) the standing rule table's `tool_demo_sandbox` matched the bare
word "sandbox" — the bridge's scenario keys/evidence prose
demo-looped every bridge-carrying conversation to its step cap — the
pattern now names the tools (`sandbox_write_file|sandbox_run|沙盒`).
2026-11-11 (d) the PACKAGE REFACTORED TO THE SEMANTIC LANE LAYOUT —
`biz_logic/` holds the reply CONTENT by graph segment (`plan/` the
generic plan shape `shapes.py` + the planning-phase cases, `custom/`
the standing step rules `rules.py` (repair verdict → tool demos →
domain findings → general tail) + the DYNAMICS walks `dynamics.py` +
the GRANULAR mistake ladders under `cases/` — sql/, sandbox/,
db_semantics/, tool_args/, step_budget/, repair/ — one feature file
per troubled process, `result/` the four review seats' lens rules +
synthesis, `reply_texts.py` the shared self-labelling builders),
`infra_setup/` the MACHINERY (`specs.py` the typed reply payloads —
the leaf module, `state.py` the conversation-state ledger + harness
fingerprints, `replies.py` the book stitching the lanes' fragments
into `DEFAULT_RULES` in the exact original order, `provider.py`,
`scenarios/` the machinery — `chain.py`/`parser.py`/`builder.py`/
`fakes.py`/`catalog.py`, loading `biz_logic/**/*.feature`). The biz
lanes import only the leaf modules; the book + the catalog are the
only infra→biz edges. The package root `__init__` keeps the ONE import
surface (every old name re-exported; the direct
`test.dummy_langgraph.replies` imports in temp_scripts updated to the
new homes); A/B-verified: the assembled 19-rule table + 13 dynamics
rules byte-identical to the pre-refactor literals
(`temp_scripts/verify_dummy_rules_ab.py`), all three contract suites
green (unit/HTTP-sim, 17/17 dynamics, 123/123 scenarios).
2026-11-12 the FEATURES REWRITTEN TO CANONICAL GHERKIN (per the
cucumber.io reference — Given = the INPUT, the conversation state the
model is handed; When = the CONDITIONS, the discriminating trigger;
Then = the EXPECTED OUTPUT, the canned reply + its keyed DocStrings;
`And`/`But`/`*` continue the preceding clause; one `Scenario` = one
turn, the `@tag` above it the turn's STABLE fixture name, file order
the chain order; the ``` fence kept — a valid Gherkin DocString
delimiter — because script bodies carry docstrings of their own; a
`Ladder:` feature's Scenarios stay descriptive prose, the expander
owns those turns). The parser (`ScenarioSpec` + `_step_keyword` +
load-time `_validate` — a scenario needs its @tag, ≥1 Given/When and
exactly one Then; ladder scenarios must carry no executable steps) and
the builder (`predicate_atom`/`turn_predicate` the atom vocabulary,
`build_turn` the Then prose — the model calls/runs/writes/patches/
replies with/fails with/proposes the plan) own the vocabulary; the
feature files carry DATA only. Turn names + payload keys unchanged —
the contract tests' fixture handles survive untouched; the
whole-process probe needed `JEV_DECISIONS=0` (the System One jev
decision lane decides repair verdicts itself when active — a probe
targeting the GENERATIVE lanes sets the kill-switch, the same env
contract probe_supervisor_repair_live.py sets). 2026-11-12 (b) the
REPAIR-PROBE DRIFT FIXED (the repair probes had not been re-run since
the 2026-11-11 resume/prompts rounds and silently degraded): the
resume block's prior-traffic teaching line (`_resume_block`: "your
PRIOR attempt's recorded traffic is chain … — search_thread_context
finds what you already asked…") matched the standing
`thread_search_demo` rule on EVERY resumed hop — the resumed
conversation looped on the demo call to the step cap, the step failed
again, the supervisor retried it, the next resume looped (the repair
probe's 8 accumulated failed `scope_anchor` rows). The fix is the
DUMMY-side teaching strip's second pattern
(`infra_setup/state.py::_RESUME_TRAFFIC_RE` — the same house mechanism
as the [Materialized data] strip; the `[Resume — supervisor
instruction]` prefix stays, the resume atoms anchor on it), beside the
harness-side RepairWorker dispatch fix (`supervisor/workers.py` — the
lane's NODE_FAILED window now reaches the picker's ask). Stale probe
chains need a DB purge before a re-run (the probes reuse a fixed
thread_id and purge only on success — the accumulated pre-fix failed
rows kept failing the `not failed` assertion);
`temp_scripts/purge_stale_repair_probe.py <thread_id>` is the one-off
cleaner. Both probes green again (repair lane e2e PASS, the
model-unreachable code-fallback lane PASS) + the whole-process probe
CLEAN. Its HTTP-SIM
form is the RESIDENT LOCAL-MODEL HOST (2026-11-11: the former
standalone FastAPI server `test.dummy_llm.server` on :8768 merged into
`server_cluster.llm` (the server cluster's llm area) as the `dummy-1` model; python -m
server_cluster.llm, port 8767), serving the SAME engine
over the real provider HTTP surface (`POST /v1/chat/completions` + the
zhipu mirror path, Bearer required → 401 code 1002) with RATE-LIMIT
ERROR injection — `POST /control {fail_next, rate_limit_code (1302 |
1701 | 1113), max_rpm, reset}` returns HTTP 429 + the zhipu error
envelope (messages carry 速率限制/并发 so `supervisor.repair`
classifies them exactly like live failures) — so the REAL transport
(retry ladder, error codes, audit rows) runs against the simulation:
`build_chat_provider("dummy", api_url=<host endpoint>)` or the seeded
always-present `llm/dummy` secret_config row (INACTIVE by
construction — activating it flips every run; its api_url + the
lmh-dummy api key now point at the host) builds `ZhipuChatProvider`
against it. Contract asserts:
`temp_scripts/test_dummy_langgraph.py` (unit + HTTP-sim + rate-limit
classification + core-loop-over-fake-MCP); the host is ALWAYS-ON
(`_common/service_heartbeat.py::MONITORED_SERVICES` — the stream
watchdog spawns/keeps `python -m server_cluster.llm`
live, `main()` registers the heartbeat, the app-factory form never
does); integration:
`temp_scripts/probe_dummy_langgraph.py` (a full three-graph chain
on the dummy, 10/10 distinct replies, chain purged after). LANGGRAPH
AND AGENT TESTS RUN ON THE DUMMY — construct `DummyLanggraphProvider`
(unit/duck-typed) or `--provider dummy` (HTTP-sim against the resident
host, no --dummy-url needed since 2026-11-11); a probe that calls a
live provider must justify itself.
2026-11-12 (c) THE JEV SCENARIOS + the runner's decision-engine SEAM
(the System One lanes became offline-walkable): the scenario catalog
grew the two jev features — `jev-fast-path-verdict` (the flake walk
whose verdict the INJECTED engine answers: NO verdict turn exists —
the defining property; arms on `[flake_state]` with its own marker,
mount-disjoint from transient-flake/flake-verdict) and
`jev-spawn-gate-declined` (the spawn breaker's gate at the second
traceback answered a confident NO — NO repair child, the parent
patches the remaining bug itself; explicit turns, not a `Ladder:` —
the ladder walk always spawns; arms on `[momentum_state]`). The STUB
engine `test/dummy_langgraph/jev.py::
StubJevDecisionEngine` (resume=/spawn= taking yes | no | below_floor |
None-unavailable; REAL `JevDecision` answers in the post-interpret
vocabulary the call sites gate on; every read recorded) rides
`run_thread(..., decision_engine=...)` / `DeepAgent(
decision_engine=...)` — the NEW `decision_engine=` seam on
run_thread/SuperGraphRunner + the two graph builders (the `sandbox=`
seam's twin; an injected engine wins over `JevDecisionEngine.active()`
at both wiring sites, so NO env knob is needed and the resident host
is never contacted). `test_dummy_langgraph_scenarios.py` grew the jev
walks (18 chains now) + the E2E declined-spawn run over the stub (the
gate re-asks on every turn the streak holds — edits are neutral, only
a green run resets — so 2 reads per declined walk is the real shape);
`temp_scripts/probe_dummy_langgraph_jev.py` walks the WHOLE three-graph
chain with resume=yes + spawn=no and asserts: 0 generative verdict
asks, 1 jev resume read carrying the timeout, the resumed hop +
flake completed, 2 declined spawn reads at streak 2, 0 handoff/fold
conversations, the parent finished (3 sandbox runs), the System One
review row PERSISTED (kind 5, `[System One verdict]`) — which fixed a
real bug en route: the fast path's observability write referenced an
out-of-scope `facts` name (`supervisor/workers.py`), so every
successful jev verdict's supervisor-review row silently died on a
fail-soft NameError.
2026-11-03:
the RUN LIMITS (conversation step budgets, recursion bound, plan-tree
caps, spawn wave/semaphore/depth, per-request output ceiling,
findings-bridge + eager-prompt budgets) moved from scattered module
constants into ONE static config file —
`database/sql/monitoring/static/deep_agent_config.json`, read fail-soft
per run by `deep_agents/limits.py::run_limits()` (mtime-cached; edits
apply on the next run, no daemon restart). The AI page's Run Limits
panel (`data_viz/src/analysis/pages/Ai/RunLimitsPanel.tsx`) edits it
through GET/PUT `/api/config/deep-agent`; the bridge's per-run
`max_steps` reads the same file. 2026-11-03: the LARGE-PAYLOAD
PRE-CHECK (`deep_agents/payload_guard.py`, package root beside
`limits.py`, stdlib+limits only so every layer imports it) — every
message builder (`core.tools.user_content`, `langgraph.prompts.
step_user_message`, `planning.planner.plan_user_content`) runs
`guard_payload` before serializing the structured context: a subtree
whose serialized size passes `payload_entry_max_chars` ships as a
`_guarded` metadata + preview stub (children guarded first, so a
container of guarded children stays navigable; oversized strings
truncate in place), `payload_guard_chars` gates the whole block
(largest top-level entries trimmed to it), and a `[Large data
guarded …]` block after the JSON fence NAMES every elided path and
prompts the model to re-fetch and CHECK the real data through its
tools (db_run_query over the item's db_keywords recipe) — never
answer from previews. The loop's tool-result seam head+tail-caps one
result's text at `tool_result_max_chars` (a 500-row db_run_query cap
is rows, not bytes). Driver: the measured 76 KB data block riding
every step hop. Serving: FastAPI daemon :8766
(`deep_agents/server.py`, `/run /stop /active /ws`) bridged to the UI by
`data_viz/api/services/ai-summary.service.ts`. Observability: per-LLM-call
rows in `llm.llm_requests` with usage tokens + chain linkage
(`_core/request_log.py`), bounded event bus (`deep_agents/events.py`),
per-node metrics (`langgraph/state_store.py::NodeRunWriter`).
2026-11-10: the per-context token rollup is CACHE-AWARE — a conversation
chain resends its accumulated history every tool turn, so the naive
`input_tokens` sum double-counts it; `database/sql_views/data_viz/api/
services/ai-thread/llm_usage.sql` chains each row to its writer-backfilled
`prev_request_id` and reports `cache_tokens` (Σ least(prev_in, in) over
usage-carrying consecutive pairs — the re-sent prefix the provider's
context cache serves, a subset of the naive sum) + `fresh_input_tokens`
(input − cache — the genuinely new input; == the chain's LAST request's
input for append-only conversations, verified 39/39 live chains); the
Thread Graph's token tail renders it as `… tok (in · out · cached)`.
2026-11-12: the audit rows carry the PROVIDER TYPE and the OFFLINE lane
records consumption — (a) `llm_requests.provider` now records what the
secrets registry LOADED: `llm_config.build_chat_provider` threads the
row's `ext_llm_provider` label through `BaseLlmProvider(audit_name=…)`
into the transport's audit name (dummy runs stop reading as 'zhipu';
`llm_usage.sql` + the Thread Graph tails show the type:
`… · zhipu · 12.4k tok (… in · … out · … cached)`); (b) the in-process
dummy double audits every `chat_complete` through the transport's own
`record_request` seam (provider `dummy`, endpoint
`dummy://in-process/chat/completions`, the shared
`test.dummy_langgraph.estimate_usage` chars/4 numbers the HTTP envelope
reports, `is_failed` rows on injected failures, envelope-shaped
`response_payload` so the UI request rows render the same face) —
chained runs land with real chain ids so the cache-aware rollup works
on dummy data; probed by `temp_scripts/probe_dummy_token_audit.py`
(18 checks: bound-chain landing, incremental payloads, the failure row,
the view's cache/fresh split on the dummy chain).
2026-11-03 scope notes: `llm_request_timeout_sec` (default 300s) bounds
ONE INDIVIDUAL LLM REQUEST (one chat completion, its retries included) —
never a graph node: a node's step conversation makes many such requests
across its tool turns and legitimately outlasts the budget many times
over; nodes are bounded by `max_conversation_steps` and the run by
`run_timeout_sec` instead. And the parallel wave joins with LangGraph's
parallel-node semantics — the merge node after a wave runs only after
ALL of the wave's nodes finish: the runner's `asyncio.gather` gates the
lane (a failed member settles its row failed and STILL gates the merge,
which proceeds without its finding — no early merge; the wave stops at
the first unmarked inline step, normally the `final_view` synthesis).

2026-11-01 additions: context rows are chained to their node's ONE LLM
context (`llm.deep_agent_contexts.node/state_id/llm_context_id` — the
same uuid as `llm_requests.chain_id`, so payloads link the requests
that produced them), and every request row carries a GIN-indexed
`search_tsv` (A = incremental input, B = output; StructBERT-segmented,
writer-thread tokenization, `backfill_search_tsv_async` heals NULLs).
On top of both sits the THREAD-INTERNAL SEARCH — a
`search_thread_context` builtin in both plan kits
(`planning/thread_search.py`): any-level agent keyword-searches its own
thread's recorded traffic, scoped by chain membership, healing its
thread's unindexed rows before searching.
2026-11-06 (c) — CRITICAL DISCOVERIES + CONTEXT TAGS: every
`deep_agent_contexts` row carries `is_critical` (lifted in
`planning/store.py::_insert_context` from the payload's `"critical":
true` — the model's own mark, arriving via the spawn tick tool's
`critical` argument or the `[CRITICAL]` final reply line the
step/spawn/output prompts teach; `store.extract_critical` parses the
marker fail-soft out of raw replies at every LLM-text seam — step
settles both lanes, spawn answers, the two output docs) and `tags` (the
StructBERT-tokenized GIN-indexed vector over the payload's string
leaves, built at insert off-loop; `backfill_context_tags_async` heals
NULLs, `replace_plan_payload` refreshes it).
`search_thread_context` grew a SECOND LANE — the contexts' payloads
matched over their tags GIN, thread-scoped beside the requests'
search_tsv lane — plus `critical_only=true` (query optional): the
MUST-CHECK the prompts teach both ways (check the run's flagged
discoveries before concluding; flag your own). Partial index
`ix_deep_agent_contexts_critical` serves the flagged reads; seeds 01 +
10 refreshed with the round's prompt texts.

## Principles to PRESERVE — do not regress these

The harness already implements the hard lessons structurally, in code,
where most implementations only put them in prompts:

1. **Structural enforcement over prompt instruction.** Plan mutation
   authority is enforced by `PlanAuthority` (pending-guards, minted-id tick
   sets, runner-side ticking), not by asking the model to behave. Any new
   capability (memory writes, critic veto, spawn policy) should likewise be
   enforced in code.
2. **Fresh-context subagents, condensed results.** Each step conversation
   is fresh; findings cross hops only through the findings bridge
   (`langgraph/prompts.py::findings_block`, per-finding 1200 chars, block
   12000, aged down to title lines). Spawns inherit only run goal + business
   context, never the parent transcript.
3. **Error-as-state + always-answer.** A failed step settles its row and
   ENDS ITS PATH (2026-11-02: a failed main-path step settles the
   remaining pending main-lane rows not-run and the chain goes to
   finalize — a doomed tail never burns LLM calls; a failed spawn stays
   local, its pending subtree settling not-run with it). The run
   survives, and the SUPERVISOR WATCHDOG — independent of the graph,
   idle on healthy runs — wakes on the root-path failure and records
   its diagnosis (observe mode, no veto). Persistence is fail-soft; the
   degraded execute lane guarantees an answer even when planning never
   persisted. The run's answer is the LAST COMPLETED step's finding.
4. **Answer gate for uncollected spawns** + orphan-spawn cancellation in a
   `finally` — the exact failure mode Anthropic documents.
5. **Context hygiene as hard policy.** Transcripts/messages are never
   persisted (a `_closed()` whitelist prevents it); contexts are referenced
   by uuid; "ONE NODE, ONE LLM CONTEXT".
6. **Compute as a first-class tool.** The docker py-sandbox (read-only DB,
   caps) is the substrate for verification work — use it when building the
   critic (see roadmap).
7. **No-transcript observability.** `llm.llm_requests` + node metrics give
   decision-pattern traces without storing conversations.

## External best practices — the compressed list

Full detail in `references/best-practices.md` and
`references/deepseek-super-agent.md`. When designing or reviewing a
deep-agent change, check it against these:

- **Context is a finite budget.** Context rot is real; add tokens only when
  they pay. Prefer JIT retrieval (lightweight references + load on demand)
  over pre-loading; hybrid = pre-load the few standing prompts, explore the
  rest.
- **Teach delegation explicitly.** Each spawn goal needs objective, output
  format, tool guidance, boundaries — vague goals produce duplicate or
  misinterpreted work.
- **Scale effort to complexity.** Simple query → few tool calls; complex →
  more spawns. Token usage explains ~80% of quality variance; unbounded
  effort both costs and hurts.
- **Generator–critic with EXTERNAL verification.** Self-review alone
  reasons in circles; verify against computation (sandbox recompute,
  cross-step consistency checks), with bounded iterations.
- **Evals are the enabling investment.** ~20 representative questions +
  LLM-as-judge rubric (accuracy, citations/completeness, source quality,
  tool efficiency), judged END-TO-END, tolerate emergent behavior. Start
  before big changes, not after.
- **Errors compound.** Resumable checkpoints beat restarts; retry
  policies; let the agent adapt when told a tool is failing.
- **Artifacts in files, references in context.** Big tables/JSON go to
  files; the conversation carries paths, not payloads.
- **DeepSeek Super-agent specifics:** keep the router thin and unable to
  work; deliver events, not prompts (code filters first, model judges
  after); route follow-ups directly to the owning worker; separate
  compute / state / execution; a timed watchdog session checks liveness;
  unrequested actions start in observe mode; keep replies human-tolerable
  (short, returned immediately while work continues).
- **DeepSeek Harness mechanics worth copying:** named swap points with
  shipped implementations chosen by config (never spawn the loop to change
  it); per-model routing (premium model for the outer loop, cheap for
  mechanical sub-tasks); one generated program instead of many per-tool
  round trips (PTC/code mode) for long tool chains; append-only trajectory
  log by source; headless run-and-exit profile for CI.

## Gap roadmap — ranked, with build direction

Each item: what is missing today → how to build it in THIS repo's
conventions (SQL-first DDL in `database/sql/`, SMALLINT type codes for any
type/kind/state column, DB writes via `_common/db_commons`, sandbox changes
per `py-sandbox-env`). Update this list when an item lands.

### P1 — long-term memory (the missing pillar)

Nothing persists between threads except prompts and request logs; every run
re-derives the same index/sector facts. Build:

- `llm.deep_agent_memories` (SQL-first DDL): scoped rows (sec_type/code
  via the existing scope pattern in `llm.deep_agent_identities`), kind
  column as SMALLINT code (fact / preference / pitfall / reusable-query),
  body, source thread_id, confidence, timestamps. Purge/merge policy like
  `09_deep_agent_runs_prune.sql`.
- Write at `finalize` (`planning/store.py`): distill durable facts from the
  run's `final-result` context — model-summarized, capped, deduped.
- Inject in `planning/planner.py` template args and the step user message
  (`langgraph/prompts.py::step_user_message`) — pre-load the small standing
  set, keep bulk memory JIT-retrievable.
- Curation is structural: only finalized threads with a completed
  `final_view` step may write memories.

### P1 — critic / verification node — LANDED AS THE SUPERVISOR WATCHDOG (2026-11-02), external checks still open

The LLM critic is IN, redesigned per the 2026-11-02 directive as an
INDEPENDENT watchdog beside the graph (`supervisor/watchdog.py`, driven
from `run_thread`'s tail via an injected `NodeRunWriter` factory — NOT a graph node, no path or data lane
shared with it): idle on healthy runs (zero LLM), woken by the pure
code filter `root_path_failures` only when a root-path node failed —
one bounded call, lean input (goal + tree + failed nodes' directive/
error/note, no findings bridge), output = error diagnosis + whether
the plan/topology needs an update, recorded as the supervisor-review
context (observe mode — recommendations only). 2026-11-05: the
supervisor grew into its OWN package (`supervisor/` — engine/loop/
tools/prompts/watchdog) with its own skills (self-fetched
`deep-agent-supervisor-system` row + its kit's type-4 description
rows, fetched ONLY on the waking path) and its own diagnostic kit
(`SupervisorToolKit`: `search_thread_context` re-reads the failed
run's recorded traffic; `kg_search` resolves DB semantics / prompt
catalog for the topology verdict) over a bounded mini tool-loop
(`supervisor/loop.py` — the standalone bounded tool-round loop the
core loop's MCP coupling never allowed; 3 rounds cap, budget-spent
notices). The earlier end-of-run every-thread review design was
SUPERSEDED by this one ("no need of taking in/out every graph
data"). Still OPEN (build on the watchdog):

- External checks over self-review: sandbox-recompute numbers cited in
  findings; cross-check conflicting step claims against computation;
  verify web-sourced claims have citations. The docker py-sandbox is
  the substrate (read-only DB, caps) — the watchdog's kit now carries
  thread_search + kg_search (2026-11-05); SANDBOX recompute remains
  open.
- A repair round — LANDED (2026-11-06) as the TRANSIENT-RETRY repair
  lane (event-time poller + pause window + reopen; see the supervisor
  section). Still open on top: PLAN-SHAPE repair (applying the
  supervisor's plan-update recommendation behind PlanAuthority — split/
  merge/reorder — needs the same pause window but a plan-mutation
  authority), a HITL gate for higher-risk actions, and sandbox
  recompute as an intervention tool.

### P1 — evaluation harness

The raw material already exists — full replay from `llm.llm_requests`
chains + `deep_agent_states`. Build:

- ~20-question regression set across sec_types in `temp_scripts/` or a
  small DB table; runner replays each question through `run_thread` with
  fixed seeds/limits. Plumbing-level replays (does the chain complete,
  do the findings flow) run on the DUMMY LLM
  (`test/dummy_llm` at the project root — zero spend, deterministic, see
  `probe_dummy_langgraph.py`); only quality-level replays need the
  live provider.
- LLM-as-judge with a fixed rubric (numbers correct / completeness /
  tool efficiency / no hallucinated codes), scored 0–1, stored per run;
  trend line over the prune-job survivors.
- Gate: any harness change (prompts, limits, routing) that drops the mean
  score needs a stated reason.

### P2 — effort scaling + token budget

The limits are UI-configurable since 2026-11-03
(`deep_agent_config.json` — the panel edits them per run), but they are
STATIC per run: no complexity scaling (a tier choosing smaller/larger
budgets from plan complexity) and no per-run token ceiling (the
`max_output_tokens` knob bounds per REQUEST, not the run). Build:
complexity tier in plan-JSON (planner already classifies), tier sets
step/spawn/concurrency budgets; enforce a per-run token ceiling at the
`_core/request_log.py` choke point (usage already logged) — soft-warn
at 80%, hard-stop by refusing further provider calls.

### P2 — HITL plan approval + resume

`AsyncPostgresSaver` checkpoints persist but `run_thread` always starts
fresh (stated in the 09 DDL header); the UI thread-graph view already
renders plans. Build: LangGraph interrupt after the `plan` node → UI
approve/edit (the prompt rows are already DB-editable) → resume same
thread_id; crash resume is then free. Server-side: `/approve` endpoint
next to `/stop`.

### P2 — run artifact filesystem

Findings bridge caps force steps to inline big tables as prose. Build: a
persistent per-run artifact dir (survives the sandbox session GC — do NOT
tie it to the sandbox session), exposed as `write_artifact`/`ls`/`read`
builtins in `core/tools.py`; steps exchange paths, the findings bridge
carries references; `final-result` links the artifact list.

### P3 — smaller items

- Compaction for the degraded/inline single-conversation lanes (main lane
  already sidesteps it via fresh-per-step).
- Per-thread cost rollup: pricing table × `llm.llm_requests` usage, shown
  in the Thread Graph view.
- Citation pass for `web_search`-sourced claims (Anthropic's
  CitationAgent pattern).
- Prompt-cache friendliness: the static per-run prefix is re-sent to every
  step conversation — check Zhipu/GLM context caching support in
  `core/provider.py`.
- Thin-router serving: `server.py` currently caps parallel runs at 1;
  Super-agent numbers justify letting the run loop stay responsive while
  spawns work (already true) and batching `/run` triggers under load.
- Self-improvement: mine failed request chains monthly → propose edits to
  the DB-editable prompt rows (`llm.ai_prompts`) as a reviewable diff —
  Anthropic's highest-ROI loop (40% time cut from model-improved tool
  descriptions).

## House rules when implementing

- ANY code change → `oxpicious-dev-conventions` first; sandbox-side changes
  → `py-sandbox-env`; new DDL → `db-sql-write` + `db-smallint-type-codes`.
- New modules are class-first pydantic (`oxpicious-dev-conventions`):
  capabilities are objects (the `Tool` / `Skill` / `ToolKit` pattern
  above), payloads crossing module or HTTP boundaries are `BaseModel`s
  (the server already models its API payloads that way), not loose
  dicts — without breaking the duck-typed stub-testability below.
- New daemons/middleware register in `_common/service_heartbeat.py::
  MONITORED_SERVICES` (name = module path = spawn contract).
- Verify by running a scoped question end-to-end (`--sec-type index`
  scope) and checking `llm.llm_requests` chains + the UI Thread Graph, not
  just the code; throwaway probes in `temp_scripts/`.
- Preserve the layering rule: `core` knows nothing of planning/langgraph;
  `planning` knows nothing of langgraph; only `langgraph` composes. No cudf/pandas
  in the package.
- The pure modules (`planner`, `plan_json`) unit-test against a duck-typed
  `chat_complete` stub — keep new domain modules equally stub-testable.
- LANGGRAPH AND AGENT TESTS RUN ON THE DUMMY LLM DOUBLE
  (`test/dummy_llm` at the project root — 2026-11-12 (f), the
  request→response engine split out of `test/dummy_langgraph`, which
  keeps the kg/jev cucumber assertion datasets): construct
  `DummyLlmProvider()` (alias `DummyLanggraphProvider`) — matched
  request → the feature dataset's canned reply, per the scenario
  predicates; the kg/db tool seam (`FeatureToolSeam`) and the decision
  stub (`StubJevDecisionEngine`) still load from
  `test.dummy_langgraph`. When the REAL transport
  (retries, error codes, the repair lane's classification) is under
  test, build the provider via `build_chat_provider("dummy")` (or CLI
  `--provider dummy`) — HTTP-sim against the resident host's dummy-1
  endpoint, defaulting to
  http://127.0.0.1:8767/v1/chat/completions — rate-limit failures
  inject through `POST /control`. A test/probe that calls the
  live ZhiPu provider must justify itself; the seeded always-present
  `llm/dummy` secret_config row stays INACTIVE (activating flips every
  run).

## Keeping this skill current

- When a roadmap item lands, move it from the gap list into "The harness
  today" with its file paths, and date it like the rest of this repo
  (`2026-xx-xx`).
- Before major harness work, re-check the external landscape (LangChain
  Deep Agents changelog, Anthropic engineering blog, DeepSeek Harness
  releases — the Super-agent was published 2026-10 and is explicitly
  research code with breaking changes expected) and fold new findings into
  `references/`.
