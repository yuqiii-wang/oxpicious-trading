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
- `tools/` — the TOOL LAYER (2026-11-02): every model-callable capability
  (beside the external MCP tools) as an OBJECT — a `Tool` subclass per
  tool (schema + `run` handler + its `llm.ai_prompts` row, prompt_type
  4 `tool`: seeded from the classes' `DEFAULT_DESCRIPTION` constants by
  `static/ai_prompts/10_tool_prompts.sql`, overriding the built-in
  schema text fail-soft per run via `store.fetch_tool_prompts`),
  grouped into `ToolKit`s the runner mounts per conversation role:
  `SandboxToolSet` (the four sandbox_* tools over one `Sandbox` client),
  `MainPlanToolKit` / `ForkPlanToolKit` (the plan-mutation tools over a
  `PlanAuthority` + `ThreadSearchTool`), `StatsToolSet` (2026-11-02:
  the six `stats_*` study tools over the daily bars — ma/rsi/macd/roc/
  volume/range; house conventions, plain numpy on the read replica,
  precomputed view MA columns reused when they cover the window; the
  runner mounts it on every step conversation and the hooks stamp it
  on every fork), `KgToolSet` (2026-11-05: `kg_search` + `kg_expand`
  — the knowledge graphs `llm.kg_entities`/`llm.kg_edges` as SEMANTIC
  SEARCH INDEXES with a `namespace` argument: 0 = DB semantics (one
  entity per table; agents find tables by business meaning —
  bilingual trader vocabulary, never a schema dump) and 1 = the
  prompt catalog (one entity per cataloged skill/plan row — WHICH
  skill teaches a domain, then `load_prompt` it run-scoped);
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
  the KG CHAIN CACHE's TWO LAYERS (2026-11-06):
  `llm.kg_queries` (`database/sql/llm/14_kg_queries.sql`,
  `kg/query_log.py::QueryLog`)
  is the LOG / TWO-HIT EVIDENCE table — every successful traversal's
  chain relogged fail-soft (DELETE + INSERT, one live chain per
  (query, namespace), PK (namespace, chain_id, edge_id), query
  indexed: (query, namespace) btree + pg_trgm GIN) — and
  `llm.kg_hotpaths` (`database/sql/llm/16_kg_hotpaths.sql`,
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
  `supervisor/repair.py` (RepairChannel + the transient classifier +
  the Action parser + the structural policy gate) and
  `supervisor/poller.py::supervisor_session` (the per-run BACKEND
  POLLER the runner starts beside every graph; its exit runs the final
  sweep, absorbing the old post-run tail): the step lane, on a
  root-path failure, opens a BOUNDED PAUSE WINDOW before settling the
  doomed tail (`runner.py`'s repair gate — signal, await verdicts,
  refresh the tree); the poller wakes instantly (or on its
  `supervisor_poll_interval_sec` DB poll), classifies IN CODE
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
  (`temp_scripts/test_supervisor_repair.py`) and live-probed
  (`temp_scripts/probe_supervisor_repair_live.py`).
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
  conversation — inline steps, forked children through
  `subagent_hooks(extra_builtin_tools=…)` (recursing to sub-sub
  children), and the degraded execute lane. The planner reads the
  COMPACT data variant (`planner_scoped_data`: item identities +
  metric names, never the per-metric db_keywords recipes or docs
  bodies — steps keep the full data; 71% measured cut). Measured
  driver: an all-scopes-all-skills run's first user message was
  80,920 chars (76 KB data block) and every conversation then
  wholesale-fetched 25 skill rows ≈ 19 KB. THE THIN GENERAL PLAN
  (2026-11-03): the BROAD-RUNS clause keeps the main plan thin —
  scope entry + delegated SUB-PLAN section forks + final_view — and
  the pickable `general-plan` row (prompt_type 2, seeded in
  `static/ai_prompts/09_plan_prompts.sql`) encodes the same shape;
  each sub-plan goal opens with "First plan your own sub-steps:
  update_plan add_nodes under your fork, then work and tick them",
  NAMES its study prompt keys (auto-loaded in the child via node
  matching) and its item group — the granular biz planning happens
  INSIDE the sub-plan node on the existing ForkPlanToolKit (the fork
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
  fork / sub-plan nodes smaller goals cut from it), and a completed
  tick marks that goal FINISHED — taught across the planner system
  prompt (`plan_json.plan_system_prompt`), the step addendum (the
  reply IS the goal's settlement), the fork plan addendum, and the
  tick / fork-update tool descriptions (seeds 01/09/10 refreshed +
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
  constants) and are NEVER delegated (a fork inherits
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
  `load_decided_prompts` / `PromptBudget`), the fork pass-down packager
  (`fork_biz_context`) and `standing_system` (re-exported by
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
     when planning failed — no C/R minted, always-answer unchanged).
     Its output node = the LLM findings synthesis (today's final_view
     role) persisted as the kind `graph-output` context (agent_context_
     kind 6) — THE HANDOFF.
  3. RESULT graph (thread R, type 2) — `build_execution_graph` over
     the SEEDED type-result template row (`default-result-wave` in
     `llm.deep_agent_langgraph_templates` — DB-editable review goals;
     fail-soft to `plan_json.review_wave_steps`): the four REVIEW seats
     as one parallel wave (findings seeded with C's synthesis as line
     0), its output node = the final answer persisted as kind
     final-result. R ALWAYS runs after C completes (sequential
     ainvokes; the graphs are independent compilations).
  EVERY graph has a FINAL OUTPUT NODE (`agents/output_agent.py::
  OutputAgent` — one directive-carried synthesis conversation, stats/kg/
  prompt tools, no plan kit, no forks) — each graph's output is what
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
  langgraph package: engine/loop/tools/prompts/watchdog + the 2026-11-06
  repair lane repair.py/poller.py; the per-run SUPERVISOR SESSION — a
  backend poller started with every graph — watches node states for the
  whole run, repairs transient step failures mid-run, and its exit runs
  the final observe sweep via an injected `NodeRunWriter` factory) — an INDEPENDENT
  per-run agent beside the graph, NOT a LangGraph node, sharing no
  path or data lane with it (the DeepSeek Super-agent watchdog
  pattern: watches events, stays quiet unless worth handling; code
  filters first, the model judges only what passes). IDLE on healthy
  runs — zero LLM, zero context; the trigger is the pure filter
  `root_path_failures` over the settled node states (a failed
  top-level step or the failed planning row; fork failures stay
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
  super chain side by side, joined by the SPECIAL dashed double-arrow
  HANDOFF edge between consecutive graphs' output/entry nodes, each
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

Delegation: sync `spawn_subagent` + background forks with an explicit
`collect_subagents`, an answer gate for uncollected forks, parent-owned
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
leak across). The DUMMY LLM (`llm_agents/test/dummy_llm/` — the
`llm_agents.test` subpackage, production code never imports it) is the
offline test double: `DummyLlmProvider.chat_complete` satisfies the
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
injected failures. Its FastAPI form (`python -m
llm_agents.test.dummy_llm.server`, port 8768) serves the SAME engine
over the real provider HTTP surface (`POST /v1/chat/completions` + the
zhipu mirror path, Bearer required → 401 code 1002) with RATE-LIMIT
ERROR injection — `POST /control {fail_next, rate_limit_code (1302 |
1701 | 1113), max_rpm, reset}` returns HTTP 429 + the zhipu error
envelope (messages carry 速率限制/并发 so `supervisor.repair`
classifies them exactly like live failures) — so the REAL transport
(retry ladder, error codes, audit rows) runs against the simulation:
`build_chat_provider("dummy", api_url=<server>)` or the seeded
always-present `llm/dummy` secret_config row (INACTIVE by
construction — activating it flips every run; set its api_url for
HTTP-sim) builds `ZhipuChatProvider` against it. Contract asserts:
`temp_scripts/test_dummy_llm.py` (unit + HTTP-sim + rate-limit
classification + core-loop-over-fake-MCP); the server is ALWAYS-ON
(`_common/service_heartbeat.py::MONITORED_SERVICES` — the stream
watchdog spawns/keeps `python -m llm_agents.test.dummy_llm.server`
live, `main()` registers the heartbeat, the app-factory form never
does); integration:
`temp_scripts/probe_dummy_llm_langgraph.py` (a full three-graph chain
on the dummy, 10/10 distinct replies, chain purged after). LANGGRAPH
AND AGENT TESTS RUN ON THE DUMMY — construct `DummyLlmProvider`
(unit/duck-typed) or start the server + `--provider dummy --dummy-url`
(HTTP-sim); a probe that calls a live provider must justify itself.
2026-11-03:
the RUN LIMITS (conversation step budgets, recursion bound, plan-tree
caps, fork wave/semaphore/depth, per-request output ceiling,
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
true` — the model's own mark, arriving via the fork tick tool's
`critical` argument or the `[CRITICAL]` final reply line the
step/fork/output prompts teach; `store.extract_critical` parses the
marker fail-soft out of raw replies at every LLM-text seam — step
settles both lanes, fork answers, the two output docs) and `tags` (the
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
   capability (memory writes, critic veto, fork policy) should likewise be
   enforced in code.
2. **Fresh-context subagents, condensed results.** Each step conversation
   is fresh; findings cross hops only through the findings bridge
   (`langgraph/prompts.py::findings_block`, per-finding 1200 chars, block
   12000, aged down to title lines). Forks inherit only run goal + business
   context, never the parent transcript.
3. **Error-as-state + always-answer.** A failed step settles its row and
   ENDS ITS PATH (2026-11-02: a failed main-path step settles the
   remaining pending main-lane rows not-run and the chain goes to
   finalize — a doomed tail never burns LLM calls; a failed fork stays
   local, its pending subtree settling not-run with it). The run
   survives, and the SUPERVISOR WATCHDOG — independent of the graph,
   idle on healthy runs — wakes on the root-path failure and records
   its diagnosis (observe mode, no veto). Persistence is fail-soft; the
   degraded execute lane guarantees an answer even when planning never
   persisted. The run's answer is the LAST COMPLETED step's finding.
4. **Answer gate for uncollected forks** + orphan-fork cancellation in a
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
- **Teach delegation explicitly.** Each fork goal needs objective, output
  format, tool guidance, boundaries — vague goals produce duplicate or
  misinterpreted work.
- **Scale effort to complexity.** Simple query → few tool calls; complex →
  more forks. Token usage explains ~80% of quality variance; unbounded
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
  shipped implementations chosen by config (never fork the loop to change
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
  (`llm_agents/test/dummy_llm` — zero spend, deterministic, see
  `probe_dummy_llm_langgraph.py`); only quality-level replays need the
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
step/fork/concurrency budgets; enforce a per-run token ceiling at the
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
  forks work (already true) and batching `/run` triggers under load.
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
- LANGGRAPH AND AGENT TESTS RUN ON THE DUMMY LLM
  (`llm_agents/test/dummy_llm` — 2026-11-07): construct
  `DummyLlmProvider(script=[...])` for exact sequences or let the
  reply book match the query text; start
  `python -m llm_agents.test.dummy_llm.server` and build the provider
  via `build_chat_provider("dummy", api_url=<server>)` (or CLI
  `--provider dummy --dummy-url ...`) when the REAL transport
  (retries, error codes, the repair lane's classification) is under
  test — rate-limit failures inject through `POST /control` or
  `fail_matching=`. A test/probe that calls the live ZhiPu provider
  must justify itself; the seeded always-present `llm/dummy`
  secret_config row stays INACTIVE (activating flips every run).

## Keeping this skill current

- When a roadmap item lands, move it from the gap list into "The harness
  today" with its file paths, and date it like the rest of this repo
  (`2026-xx-xx`).
- Before major harness work, re-check the external landscape (LangChain
  Deep Agents changelog, Anthropic engineering blog, DeepSeek Harness
  releases — the Super-agent was published 2026-10 and is explicitly
  research code with breaking changes expected) and fold new findings into
  `references/`.
