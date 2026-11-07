# Local architecture map — `llm_agents/deep_agents` (as of 2026-11-01)

Full map produced by a source sweep of the repo (2026-11-01). All paths
relative to repo root. The implementation is NOT LangChain `deepagents`;
it is a self-built harness explicitly modeled on Claude Code / DeepSeek
Harness / Tencent WorkBuddy (documented in `llm_agents/deep_agents/
__init__.py`).

## Directory tree

```
llm_agents/
├── __main__.py, __init__.py        # dispatcher: `python -m llm_agents deep "…"` → deep_agents.cli
├── _core/                          # shared provider plumbing for ALL llm_agents packages
│   ├── base.py        (BaseLlmProvider: API-key resolution, retry policy, JSON POST)
│   ├── transport.py   (RetryingJsonClient, load_project_env; request_log hook point)
│   ├── request_log.py (llm.llm_requests recorder: usage tokens, chain linkage)
│   ├── errors.py, models.py (SHANGHAI_TZ), prompts.py, text_caps.py
├── kg/                             # the KNOWLEDGE-GRAPH domain (2026-11-06 migration:
│   │                              # INDEPENDENT of deep_agents — imports nothing of it)
│   ├── graph.py        (the pure namespace-blind traversal engine: bilingual
│   │                   token index + IDF seed match → spreading activation,
│   │                   per-(relation, direction) factors, fan-out-normalized,
│   │                   beam-bounded → blended rank → packed subgraph + renders)
│   ├── query_log.py    (QueryLog — the llm.kg_queries LOG / two-hit EVIDENCE table)
│   ├── hotpaths.py     (HotpathCache — the llm.kg_hotpaths PROMOTED replay layer)
│   └── service.py      (KnowledgeGraphService — TTL graph load + search/expand
│                        flows + NS_SCOPES + KgUnavailableError; the process-wide
│                        KNOWLEDGE_GRAPHS singleton is minted in __init__.py)
├── deep_agents/
│   ├── __init__.py   (architecture manifest + re-exports)
│   ├── __main__.py   (CLI entry; heartbeat autoregister)
│   ├── cli.py        (one-shot CLI, plain loop, no thread graph)
│   ├── server.py     (always-on FastAPI daemon :8766 — the UI's transport)
│   ├── events.py     (in-process pub/sub event bus for the /ws channel)
│   ├── skills/                     # the SKILL LAYER (2026-11-02)
│   │   ├── base.py       (Skill — one ai_prompts row; SkillBook — the
│   │   │                  run's set: render_all pass-down +
│   │   │                  items_block quoted-prompts builder)
│   │   └── loader.py     (SkillLoader / fetch_skill_book — the key-
│   │                      form fetch of the plan prompts, and
│   │                      from_rows — the loading ladder's
│   │                      post-verdict fetch of the decided set)
│   ├── tools/                      # the TOOL LAYER (2026-11-02)
│   │   ├── base.py       (Tool / ToolKit ABCs; BuiltinTool — the
│   │   │                  loop's runtime pair, re-exported by core.tools)
│   │   ├── sandbox_tools.py (SandboxToolSet: the four sandbox_* tools)
│   │   ├── plan_tools/   (Main/ForkPlanToolKit: update_plan,
│   │   │                  update_plan_step over a PlanAuthority +
│   │   │                  the fork addenda + ThreadSearchTool;
│   │   │                  package: addenda / common / tick /
│   │   │                  update_plan / kits)
│   │   ├── stats_tools/  (StatsToolSet: stats_ma/rsi/macd/roc/
│   │   │                  volume/range — the frequent study calcs
│   │   │                  as tools; bars / calcs / report / trend /
│   │   │                  momentum / flow / kit)
│   │   ├── kg_tools/     (KgToolSet: kg_search + kg_expand — THIN
│   │   │                  ADAPTERS (2026-11-06 migration) over the
│   │   │                  independent llm_agents/kg module: schema +
│   │   │                  argument coercion + error formatting +
│   │   │                  agent-flow hints, delegating to the
│   │   │                  process-wide KnowledgeGraphService
│   │   │                  (KNOWLEDGE_GRAPHS); the graphs llm.kg_* as
│   │   │                  semantic search indexes with a namespace
│   │   │                  argument: ns0 = DB semantics (one entity
│   │   │                  per table), ns1 = the prompt catalog (one
│   │   │                  entity per cataloged ai_prompts row); the
│   │   │                  CHAIN CACHE's two layers (2026-11-06):
│   │   │                  kg/query_log.py (QueryLog) the llm.kg_queries
│   │   │                  LOG / two-hit EVIDENCE table (PK namespace,
│   │   │                  chain_id, edge_id — query indexed, not
│   │   │                  keyed: (query, namespace) btree + trigram
│   │   │                  GIN; every successful traversal relogged
│   │   │                  fail-soft) and kg/hotpaths.py (HotpathCache)
│   │   │                  the llm.kg_hotpaths PROMOTED replay layer
│   │   │                  (same row shape + hit_count, CHECK >= 2) —
│   │   │                  kg_search replays ONLY from hotpaths:
│   │   │                  lookup exact-matches (query, namespace)
│   │   │                  with entity_hash =
│   │   │                  KnowledgeGraph.entity_digest (md5 over
│   │   │                  the entities' descriptions) and bumps
│   │   │                  hit_count; a miss runs the traversal,
│   │   │                  and find_identical_chain matching the
│   │   │                  fresh chain against ANY logged kg_queries
│   │   │                  chain (same namespace + hash, any query
│   │   │                  text, edge-for-edge in depth order,
│   │   │                  before the relog) is the SECOND identical
│   │   │                  production that promotes the query at
│   │   │                  hit_count 2; replays render via
│   │   │                  render_replay against the live graph —
│   │   │                  hops is not identity) without re-running
│   │   │                  the walk; mounted
│   │   │                  beside StatsToolSet on every
│   │   │                  step / fork / degraded lane)
│   │   ├── thread_search.py (ThreadSearchTool — schema object)
│   │   ├── prompt_tools.py (LoadPromptTool / PromptLoadToolKit — the
│   │   │                  ladder's DYNAMIC rung: one cataloged
│   │   │                  skill's instruction on demand, per-run
│   │   │                  cache; rides every conversation)
│   │   └── delegation_tools.py (spawn/collect schema objects;
│   │                      handlers stay loop methods)
│   ├── core/                       # the RUNTIME
│   │   ├── loop.py       (DeepAgent — the agent loop)
│   │   ├── delegation.py (spawn_subagent/collect_subagents contract, hooks)
│   │   ├── tools.py      (BuiltinTool dataclass, user_content assembly)
│   │   ├── provider.py   (ZhipuChatProvider — OpenAI-style tool calling)
│   │   ├── mcp.py        (McpToolClient — streamable-HTTP or stdio MCP client)
│   │   └── sandbox.py    (Sandbox — client of the docker py-sandbox middleware)
│   ├── planning/                   # the LIVING-PLAN domain
│   │   ├── plan_json.py  (plan-JSON contract: parse/validate/repair prompt,
│   │   │                 REVIEW_WAVE seats — since 2026-11-06 the RESULT
│   │   │                 graph's fixed wave, no longer the plan's closing
│   │   │                 section; strip_closing_wave for legacy templates)
│   │   ├── planner.py    (Planner propose→validate→correct session)
│   │   ├── templates.py  (langgraph-templates fetch/materialize: type-plan
│   │   │                 loads + the type-result wave seats)
│   │   ├── store.py      (llm.deep_agent_states/contexts/identities
│   │   │                 persistence; super_thread_id chain reads/purge)
│   │   └── authorities.py(PlanAuthority permission model; update_plan tools)
│   ├── agents/                     # the AGENT LAYER (one agent == one node)
│   │   ├── plan_agent.py, step_agent.py, output_agent.py (the graphs'
│   │   │                 FINAL OUTPUT NODE seats), supervisor_agent.py
│   └── langgraph/                  # the ORCHESTRATION
│       ├── runner.py     (build_plan_graph, build_execution_graph,
│       │                 settle_thread — the graph builders)
│       ├── super_run.py  (SuperGraphRunner/run_thread — the THREE-GRAPH
│       │                 CHAIN orchestrator: plan → custom → result,
│       │                 three threads under one super_thread_id)
│       ├── prompts.py    (per-step prompt surfaces, findings bridge,
│       │                 the output-node directives)
│       ├── hooks.py      (subagent_hooks, ForkBroker)
│       └── state_store.py(DeepAgentStore BaseStore, NodeRunWriter)
├── sandbox/            # dockerized py-sandbox middleware (separate MCP daemon :8789)
│   ├── server.py, runner.py, docker_api.py, Dockerfile
├── mcp/                # read-only DB MCP server :8765 (readonly_server.py)
├── llm_ask/            # plain chat agent; backs the data_viz "AI Ask" chart adviser
├── test/dummy_llm/     # THE DUMMY LLM (2026-11-07) — offline test double:
│   │                   # provider.py (DummyLlmProvider — canned chat_complete
│   │                   # + complete_from_payload envelope; script=/fail_matching=),
│   │                   # replies.py (DummyReplyBook — DIFF query-text → DIFF
│   │                   # reply rule catalog, planning shape, shape fallbacks,
│   │                   # TOOL:name directive), server.py (FastAPI provider
│   │                   # SIMULATION :8768 — Bearer 401, 429 rate-limit errors
│   │                   # with zhipu codes 1302/1701/1113 via POST /control,
│   │                   # so the REAL transport runs against it). Production
│   │                   # code never imports this package.
└── online_search_summary/, news_digestions/, llm_qa_by_research/, llm_qa.py
```

## The runtime modes

- `python -m llm_agents.deep_agents` → `deep_agents/cli.py:main()`:
  argparse one-shot run of `DeepAgent` over MCP tools; flags `--mcp-url/
  --stdio`, `--model`, `--max-steps` (12), `--system`, `--temperature`,
  `--data/--data-file` (structured JSON context; file deleted after read),
  `--no-subagents`, `--no-sandbox`, `--subagent-timeout`,
  `--max-concurrent-subagents` (4), `--json` (`@@DEEP_AGENT_JSON@@{…}`
  marker line for the API bridge), `--list-tools`, `--show-transcript`.
  Plain loop only — no plan/graph.
- `python -m llm_agents.deep_agents.server` → `deep_agents/server.py`
  (port 8766, watchdog-supervised): `POST /run` (mints thread_id, runs
  `runner.run_thread`, returns `{answer, steps, n_tool_calls, model,
  thread_id, plan, stopped}`), `POST /stop` (task cancellation by thread
  or all), `GET /active`, `WS /ws` (event stream), `GET /health`.

## The loop (`core/loop.py::DeepAgent.run`)

ONE message list, ONE provider call per step; tool calls executed via
`asyncio.gather` (sibling calls of one step run concurrently, per-call
exceptions become tool text). Loop ends when the model replies without
tool calls, or `RuntimeError` at the `max_steps` limit. No ReAct parsing —
pure OpenAI-style function calling. System prompt default =
`SUBAGENT_SYSTEM_PROMPT + DELEGATION_GUIDANCE`; with a `Sandbox` attached,
`SANDBOX_PROMPT_ADDENDUM` is appended.

## Orchestration (`langgraph/super_run.py::SuperGraphRunner` + `runner.py` builders)

ONE RUN = THE THREE-GRAPH CHAIN (2026-11-06) — three StateGraphs, three
threads (P plan / C custom / R result), one `super_thread_id` (= P's
id) stamped on every row; R always waits for C; every graph closes on
its FINAL OUTPUT NODE (`agents/output_agent.py` — the plan payload /
the graph-output findings synthesis / the final-result answer):

```
P: START → plan → output → END                       (build_plan_graph)
C: START → (plan_ok?) → (step|compact)* → finalize → output → END
          └→ execute (degraded lane in P, no C/R minted)   (build_execution_graph)
R: START → step (the review wave, seats from the seeded type-result
       template) → finalize → output → END               (no degraded lane)
C.output ──handoff──> R.entry   (the UI draws this as the special edge)
```

- **plan node**: `Planner.propose_batches()` — propose→validate→correct
  (default 2 repair rounds, temperature 0.2), validated by
  `parse_plan_json`; streamed persistence via
  `planning/store.py::open_custom_thread()` (P's partial plan context +
  C's step rows in ONE connection) + `add_plan_states`. Any failure is
  fail-soft → degraded lane.
- **step node** (one looping node; each hop = one superstep = one planned
  step = its OWN LLM conversation): the router re-reads the fresh plan
  tree between hops, so replans steer the very next hop. Inline step =
  fresh `DeepAgent` with user message from
  `langgraph/prompts.py::step_user_message()` (goal + structured context +
  selected prompt texts + step directive + accumulated findings);
  delegated (fork) step = `agent.run_delegation(goal)` with no main
  conversation. Per-hop `max_steps` clamped to `max(4, min(run_max_steps,
  20))` (tuned after live runs showed 12 starving delegated data pulls).
- **parallel wave + its join** (2026-11-03): consecutive
  delegated-or-parallel steps at the lane head run as ONE concurrent wave
  (`asyncio.gather` under `max_parallel_delegated_steps` slots);
  LangGraph's parallel-node join semantics hold — the merge node after
  the wave (the first unmarked inline step, normally `final_view`) runs
  only after ALL wave members settled, a failed member included (it
  settles failed and still gates the merge, which proceeds without its
  finding — no early merge).
- **execute node**: degraded one-conversation backstop when the plan never
  persisted.
- **finalize node**: `settle_thread()` (in_progress→failed, pending→
  skipped), no LLM.
- **result node**: persists one `final-result` context row; the run's
  answer = the LAST COMPLETED step's finding (`update["answer"]` only on
  success).
- Error-as-state routing: a step failure settles its row failed and the
  chain advances; the original exception rides `_RunContext`; only a
  degraded-execute failure is re-raised by `run_thread`.
- `config = {"recursion_limit": 80}` (default 25 cannot hold a 12-step
  plan + replans).
- Cancellation: server `/stop` cancels the asyncio task; `CancelledError`
  handlers settle node items and thread rows ("stopped by user").

## Subagents (`core/delegation.py`, `core/loop.py::_run_subagent`)

- Tools: `spawn_subagent` (goal, max_steps, `background: bool`) and
  `collect_subagents` (run_ids/wait/timeout). Sync spawn awaits the child;
  background forks are detached, supervised by `_supervise_background`,
  parked in `_background_results` until collected.
- Parallelism: sibling tool calls via `asyncio.gather`; fan-out capped by
  `asyncio.Semaphore(max_concurrent_subagents)` (default 4).
- Answer gate: finishing while background forks are uncollected gets a
  nudge to `collect_subagents`; last-step stragglers cancelled; a `finally`
  cancels all orphan forks.
- Supervision is parent-owned, never model-controlled: wall-clock
  `subagent_timeout`, concurrency cap, three interception surfaces —
  `SubagentInputHook` (rewrite/veto via `SubagentSpawnRejected`),
  `SubagentOutputHook` (rewrite result), `SubagentRunWrapper` (contextvars
  binding). Policy propagates to children.
- Recursion: `MAX_SPAWN_DEPTH = 2`; at the cap the spawn tool is simply
  not offered (structural gating).
- Context: fresh — only the goal plus `SubagentRequest.data`; the graph
  path stamps the business context pass-down (`langgraph/hooks.py::
  subagent_hooks`, `_fork_biz_context` in `runner.py`), recursing to
  sub-sub children.
- Merge: child answer returns as tool text (`format_subagent_result`,
  run id first); in the thread each child becomes a child state row under
  its step (`create_child_state`, slug `fork_<content-words>`), its answer
  a `subagent-result` context row. Roll-up is one level.

## Tools

- **DB MCP server** (`llm_agents/mcp/readonly_server.py`, daemon
  :8765/mcp, `readonly_analytics` role, row cap 500): `db_run_query`
  (client-side read-only SQL guard, single statement), `db_list_tables`,
  `db_describe_table`, `text_search` (GIN-indexed segmented tsvector over
  text.news / news_comments / news_digestions / llm_qa / …; 2026-11-02
  `order_by`: rank | date | votes (thumbs-up, news + news_comments) |
  comments (most-discussed news, the count riding back as n_comments)),
  `web_search`,
  `web_summarize` (ZhiPu web-search provider stack, persisting nothing).
- **Built-ins** (the TOOL LAYER `deep_agents/tools/` — a `Tool` object
  per tool, mounted as `BuiltinTool` pairs; 2026-11-02 refactor):
  `spawn_subagent`, `collect_subagents` (schemas in
  `tools/delegation_tools.py`; the HANDLERS are loop methods —
  supervision state lives in `core/loop.py`); `sandbox_write_file` /
  `sandbox_run` / `sandbox_read_file` / `sandbox_list_files`
  (`tools/sandbox_tools.py::SandboxToolSet` over the `core/sandbox.py`
  client — chain: builtin → `llm_agents.sandbox` MCP middleware :8789 →
  container runner :8788 → subprocess in `sagacient/cutepandas`; one
  session per run, lazily minted under a lock, shared with subagents;
  DB inside the sandbox read-only via preset `PG*` env; caps 120s/600s
  timeout, 512 KB write, 1 MB read); `update_plan` (main lane) and
  `update_plan_step` + `update_plan` (sub kit) —
  `tools/plan_tools/` over `planning/authorities.py::PlanAuthority`;
  `search_thread_context` (`tools/thread_search.py` over
  `planning/thread_search.py`'s search body — since 2026-11-06 (b) a
  TWO-lane search: the requests' `search_tsv` lane AND the contexts'
  `tags` lane, the GIN-indexed StructBERT vector over each
  `deep_agent_contexts` payload, thread-scoped; `critical_only=true`
  lists the run's `is_critical` rows — the CRITICAL-DISCOVERIES
  must-check the step/fork/output prompts teach, flagged by the model
  via the tick tool's `critical` arg or the `[CRITICAL]` reply-line
  marker `planning/store.py::extract_critical` parses). Every tool
  carries an ai_prompts row (prompt_type 4) whose active instruction
  overrides the tool's built-in schema description at mount time
  (fail-soft to the class's `DEFAULT_DESCRIPTION`); step lifecycle
  ticking is RUNNER-side, not a model tool.

## Prompts

Inline Python constants, optionally overridden by DB rows
(`llm.ai_prompts`; seed
`database/sql/text/llm/static/ai_prompts/01_deep_agent_prompts.sql`;
keys resolved at run start via `fetch_agent_prompts()`, fail-soft to
constants):

- `core/delegation.py`: `SUBAGENT_SYSTEM_PROMPT` (focused read-only DB
  research agent; inspect schemas first; narrow SQL; concise answer in
  user's language), `DELEGATION_GUIDANCE` (prefer delegating
  self-contained sub-questions; siblings run in parallel).
- `planning/plan_json.py`: `PLAN_SYSTEM_PROMPT` — SHORT ordered plan of
  BUSINESS steps (≤12; ≤6 delegated), snake_case business node tags (ops
  verbs like `fetch_`/`load_`/`query_` rejected by validation), concrete
  question titles ≤160 chars, `delegate.goal` self-contained fork
  instructions; since 2026-11-06 the plan is the BUSINESS MIDDLE ONLY —
  the four review steps and the final answer run in the run's separate
  RESULT graph, and neither the prompt nor the plan lane teaches, nudges
  or floors them anymore (`_review_fixup` stays as defensive parse
  normalization; `strip_closing_wave` drops the legacy closing section
  from saved-template loads). No ops/bookkeeping steps.
- `langgraph/prompts.py`: `STEP_SYSTEM_ADDENDUM` — "you are ONE research
  step"; self-contained finding with numbers/verdict; REPLAN via
  update_plan (only not-yet-run steps; terminate own forks); FORKS via
  background spawn + collect before answering. Plus the two OUTPUT-NODE
  DIRECTIVES (2026-11-06 inline; DB-EDITABLE since 2026-11-07 as the
  type-3 rows `deep-agent-custom-output-directive` /
  `deep-agent-result-output-directive`, the constants the fail-soft
  fallbacks): `CUSTOM_OUTPUT_DIRECTIVE` (the custom graph's
  findings-synthesis charter — the graph-output handoff document) and
  `RESULT_OUTPUT_DIRECTIVE` (the final answer weighing the findings AND
  the four reviews); each text OPENS WITH THE OUTPUT NODE'S OUTLINE in
  the run's three-graph langgraph chain (what runs before it, what its
  document persists as, which result-graph seats read it). The runner
  resolves `agent_prompts.get(KEY) or spec.directive` at the output
  node (`langgraph/runner.py::output_node`).
- `tools/plan_tools/addenda.py`: `SUBAGENT_PLAN_ADDENDUM` (extend only your
  own fork), `SUBAGENT_SPAWN_ADDENDUM` (sub-sub spawning + gather duty).
- `core/sandbox.py`: `SANDBOX_PROMPT_ADDENDUM` — prefer ONE computational
  script over many MCP queries; psycopg is read-only; use `text_search`
  for Chinese keyword search (raw ILIKE matches nothing).
- Dynamic templates: `{{sec_type}}/{{code}}/{{name}}/{{question}}`
  substituted from `data.scope` (`planning/planner.py::template_args/
  render_template`); user-selected "plan prompts" (prompt_type 2) are
  rendered once per run and passed to every step conversation and fork.
- GRADUAL INCREMENTAL DISCRETIONARY PROMPT LOADING (2026-11-02,
  `planning/prompt_loading.py` + the plan node in `langgraph/runner.py`):
  which picked SKILLS rows (prompt_type 1) load at all is a ladder —
  catalog previews (`store.fetch_prompt_catalog`, instruction LENGTHS
  only) → pure keyword match (`match_prompt_labels`: bound_labels
  force-load, run-scoped `prompt_labels` overrides, item labels in the
  haystack) → past the eager budget (≤4 skills or ≤4000 chars) the
  planner's `load_prompts` verdict (`plan_json.parse_plan_reply` —
  optional plan-JSON key; the catalog block rides the planner's user
  message). 2026-11-07: the HOTPATH CORROBORATION rung sits between
  keyword match and verdict — `planning/kg_prompts.py::
  HotpathPromptProbe` trigram-probes the PROMOTED `llm.kg_hotpaths`
  chains similar to the run's needles (question/scope/item labels) via
  `PLAN_STORE.fetch_hotpath_evidence`, resolves their edges through the
  TTL-cached live graphs (stale `entity_hash` chains skipped), and an
  optional-labeled row implicated by the evidence (its prompt_key names
  a promoted ns1 chain entity, or its optional labels hit the chains'
  bilingual entity vocabulary) returns the `"hotpath"` verdict and
  LOADS like a bound force (precedence forced > hotpath > matched;
  `prompt_hotpath_rows` cap, 0 disables). The decided set's texts land
  in `ThreadState.prompt_texts`
  AT THE PLAN NODE (not run start — the verdict exists only after the
  proposal); the rest ride `prompt_catalog` (prompt_key → preview line)
  into every step hop's message (a per-step by-key fetch menu — never
  the wholesale `prompt_type = 1` fetch the old goal text taught) and
  every fork's `data.prompt_catalog`. The planner reads
  `planner_scoped_data(data)` (item identities + metric names, no
  per-metric `db_keywords` recipes, docs as id+title — measured 71%
  cut); step hops keep the FULL data. The plan payload records
  `prompts_loaded`/`prompts_catalog` provenance. 2026-11-03: the
  ladder is DYNAMIC graph-wide — `node_prompt_matches` (labels on a
  node's OWN directive + study prompt keys the directive/goal NAMES)
  auto-loads at every inline hop (`[Step-matched prompts]` block) and
  every delegated goal (merged into the child's pass-down); the
  catalog-gated `load_prompt` tool (`tools/prompt_tools.py`) rides
  every conversation (inline steps, fork children via
  `subagent_hooks(extra_builtin_tools=…)`, the degraded execute
  lane); and the THIN GENERAL PLAN (`general-plan`, prompt_type 2)
  keeps the main plan to scope entry + sub-plan section forks that
  NAME their study prompt keys + final_view — the granular planning
  happens inside each sub-plan fork (ForkPlanToolKit add_nodes, the
  fork addendum's plan-first sentence).
- Prompt management: type-0 row = standing system prompt; type-3 internal
  rows = machinery prompts; type-4 tool rows (2026-11-02) = tool
  descriptions, seeded from the `tools/` classes by
  `static/ai_prompts/10_tool_prompts.sql` (drift-checked by
  `temp_scripts/check_tool_prompts.py`); editable in the UI prompt view
  (`PUT /api/ai/prompts/:prompt_key`).

## State / persistence

PostgreSQL-first (DDL `database/sql/text/llm/07_deep_agent_states.sql`,
`10_deep_agent_store.sql`, `09_deep_agent_runs_prune.sql`):

- `llm.deep_agent_states` — one row per node/task state (thread_id,
  parent_state_id self-FK for fork subgraphs, node, title, status
  pending→in_progress→completed/failed/skipped, ordinal, context refs,
  llm_context_id).
- `llm.deep_agent_contexts` — JSONB payloads (plan JSON, step notes,
  subagent results, final-result document).
- `llm.deep_agent_identities` — scope (sec_type/code) ↔ thread_id
  reverse index.
- `planning/store.py` — mutation API: `create_thread`, `add_plan_states`,
  `revise_pending_state`, `skip_pending_states`, `tick_step`,
  `create_child_state`/`finish_child_state`, `record_final_result`,
  `finalize_thread`, `purge_thread`, prompt/identity accessors. Every
  write publishes an event.
- LangGraph: `AsyncPostgresSaver` checkpointer (per-superstep snapshots in
  the `langgraph` schema — "interrupt/resume groundwork"; `run_thread`
  always starts a fresh thread, so NO resumability in practice yet,
  stated in the 09 DDL header) + `langgraph/state_store.py::DeepAgentStore`
  (a real `BaseStore` over `llm.deep_agent_store_items`,
  langgraph-checkpoint-postgres 3.x ships no AsyncPostgresStore) holding
  runner-lane node states (`node:plan|step-<id>|execute|finalize|result`)
  and the LLM-context REGISTRY — one uuid context per node execution
  ("ONE NODE, ONE LLM CONTEXT"); a `_closed()` whitelist structurally
  prevents message payloads from ever being stored.
- All persistence fail-soft ("persistence may never fail the summary");
  persisted `ThreadState` carries counts and bounded strings, never
  transcripts (those ride `_RunContext` in-memory only).
- History hygiene: `09_deep_agent_runs_prune.sql` keeps the best closed
  run per operational day, purges the rest (request chains → states →
  contexts → store items → langgraph checkpoints).

## Integration

- **data_viz UI**: `data_viz/api/services/ai-summary.service.ts` bridges
  `POST /api/ai/summary` → daemon `/run` (node:http, 15-min deadline,
  keep-alive; parallel-run cap from
  `service_config/llm_agents/deep_agents/server.json` `max_parallel_runs`,
  default 1); `ai-events.service.ts` holds ONE upstream `/ws` connection
  and fans frames to browsers at `/api/ai/events`; `ai-thread.service.ts`
  reads one thread's full graph for the Thread Graph view; `routes/ai.ts`
  exposes summary/stop/active/summary-runs. This is the "Analysis
  Summary" (Generate) feature — the deep agent's primary product.
- **Watchdog**: `llm_agents.deep_agents.server`, `llm_agents.mcp`,
  `llm_agents.sandbox` are always-on services in
  `_common/service_heartbeat.py::MONITORED_SERVICES` (name = module path =
  spawn contract); `database/stream_watchdog.py` starts/restarts them on
  stale heartbeats.
- **Model/provider**: `core/provider.py::ZhipuChatProvider` — ZhiPu
  BigModel OpenAI-compatible endpoint `https://open.bigmodel.cn/api/paas/
  v4/chat/completions`, Bearer auth, `DEFAULT_MODEL = "glm-5.3-flash"`,
  120s timeout, keys from `.env` (`GLM_ONLINE_SEARCH_KEY`/`ZHIPU_API_KEY`/
  `BIGMODEL_API_KEY`); retry on HTTP 429/502/503/504 and ZhiPu codes
  1701/1234 (`_core/base.py`, `_core/transport.py`). 2026-11-07: the
  provider is picked by LABEL — `deep_agents/llm_config.py::
  build_chat_provider(label)` (zhipu | dummy; the active
  static.secret_config llm row's own `ext_llm_provider` label, an
  explicit `RunRequest.provider` / CLI `--provider` wins over it);
  `dummy` = the offline test double — in-process `DummyLlmProvider`
  (canned replies, zero spend), or HTTP-SIM when the row/CLI carries an
  `api_url` (the REAL transport against `python -m
  llm_agents.test.dummy_llm.server`, the :8768 provider simulation
  with rate-limit error injection). Langgraph/agent TESTS run on the
  dummy (see the SKILL.md house rule); the seeded always-present
  `llm/dummy` secret_config row stays INACTIVE.
- **DB**: everything via `_common.db_commons` plain-SQL rung (asyncpg,
  lazy import for the cudf activation-order rule).

## Observability

- `_core/request_log.py` — one `llm.llm_requests` row per LLM call (full
  request payload + final response JSONB, status/duration, input/output/
  total usage tokens), hooked at the single transport choke point
  `RetryingJsonClient.post_json`; chain linkage (`bind_request_chain`)
  stores incremental payloads across a flow; never-blocks (bounded queue,
  daemon writer, drops rows on DB outage). The deep agent binds chain_id
  = the node's LLM context id → per-node/per-task request views join
  cleanly.
- `deep_agents/events.py` — bounded per-subscriber queues (maxsize 256,
  drop-oldest); event types `thread_started`, `state_created/updated`,
  `states_settled`, `node_finished`, `result_recorded`,
  `thread_settled`; notifications only, DB is source of truth.
- `NodeRunWriter.finish()` records `n_llm_calls`, `n_tool_calls`,
  `n_states`, `answer_chars` per node item. No cost tracking (no pricing).
- Logging via `_common/log_setup`; CLI `--show-transcript` prints every
  tool call and the subagent registry.

## Evaluation / testing of the agent itself

No pytest/suites for the deep agent. What exists:
`temp_scripts/deep_agent_states_roundtrip.py` (round-trip test of
`planning.store` + `parse_plan_json` against live tables),
`temp_scripts/probe_final_result_roundtrip.py`, `probe_chains.py` /
`extract_chains.py` + `temp_scripts/deep_agent_chains.md` (a saved
389-step chain transcript used as a manual inspection artifact). Design
notes state the pure modules (`planner`, `plan_json`) unit-test against a
duck-typed `chat_complete` stub.

## Known gaps (confirmed by the source sweep)

No memory across threads; no verification/critic pass; no eval harness;
no resume from checkpoints; no effort scaling or token budget; no
artifact filesystem for run outputs; no cost rollup; no mid-flight fork
steering (only cancel + collect); no token streaming (state lifecycle
events only). See the SKILL.md roadmap for the ranked build plan.
