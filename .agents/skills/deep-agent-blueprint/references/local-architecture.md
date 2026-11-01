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
│   ├── request_log.py (text.llm_requests recorder: usage tokens, chain linkage)
│   ├── errors.py, models.py (SHANGHAI_TZ), prompts.py, text_caps.py
├── deep_agents/
│   ├── __init__.py   (architecture manifest + re-exports)
│   ├── __main__.py   (CLI entry; heartbeat autoregister)
│   ├── cli.py        (one-shot CLI, plain loop, no thread graph)
│   ├── server.py     (always-on FastAPI daemon :8766 — the UI's transport)
│   ├── events.py     (in-process pub/sub event bus for the /ws channel)
│   ├── core/                       # the RUNTIME
│   │   ├── loop.py       (DeepAgent — the agent loop)
│   │   ├── delegation.py (spawn_subagent/collect_subagents contract, hooks)
│   │   ├── tools.py      (BuiltinTool dataclass, user_content assembly)
│   │   ├── provider.py   (ZhipuChatProvider — OpenAI-style tool calling)
│   │   ├── mcp.py        (McpToolClient — streamable-HTTP or stdio MCP client)
│   │   └── sandbox.py    (Sandbox — client of the docker py-sandbox middleware)
│   ├── planning/                   # the LIVING-PLAN domain
│   │   ├── plan_json.py  (plan-JSON contract: parse/validate/repair prompt)
│   │   ├── planner.py    (Planner propose→validate→correct session)
│   │   ├── store.py      (text.deep_agent_states/contexts/identities persistence)
│   │   └── authorities.py(PlanAuthority permission model; update_plan tools)
│   └── graph/                      # the ORCHESTRATION
│       ├── runner.py     (build_thread_graph, run_thread, settle_thread)
│       ├── prompts.py    (per-step prompt surfaces, findings bridge)
│       ├── hooks.py      (subagent_hooks, ForkBroker)
│       └── state_store.py(DeepAgentStore BaseStore, NodeRunWriter)
├── sandbox/            # dockerized py-sandbox middleware (separate MCP daemon :8789)
│   ├── server.py, runner.py, docker_api.py, Dockerfile
├── mcp/                # read-only DB MCP server :8765 (readonly_server.py)
├── llm_ask/            # plain chat agent; backs the data_viz "AI Ask" chart adviser
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

## Orchestration (`graph/runner.py::build_thread_graph`)

LangGraph `StateGraph(ThreadState)`:

```
START → plan → (step)* → finalize → result → END
             └→ execute (degraded lane, no plan) → finalize
```

- **plan node**: `Planner.propose()` — propose→validate→correct (default 2
  repair rounds, temperature 0.2), validated by `parse_plan_json`;
  persisted via `planning/store.py::create_thread()`. Any failure is
  fail-soft → degraded lane.
- **step node** (one looping node; each hop = one superstep = one planned
  step = its OWN LLM conversation): the router re-reads the fresh plan
  tree between hops, so replans steer the very next hop. Inline step =
  fresh `DeepAgent` with user message from
  `graph/prompts.py::step_user_message()` (goal + structured context +
  selected prompt texts + step directive + accumulated findings);
  delegated (fork) step = `agent.run_delegation(goal)` with no main
  conversation. Per-hop `max_steps` clamped to `max(4, min(run_max_steps,
  20))` (tuned after live runs showed 12 starving delegated data pulls).
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
  path stamps the business context pass-down (`graph/hooks.py::
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
  text.news / news_comments / news_digestions / llm_qa / …), `web_search`,
  `web_summarize` (ZhiPu web-search provider stack, persisting nothing).
- **Built-ins** (`core/tools.py::BuiltinTool`, dispatched before MCP
  fallback): `spawn_subagent`, `collect_subagents`;
  `sandbox_write_file` / `sandbox_run` / `sandbox_read_file` /
  `sandbox_list_files` (`core/sandbox.py::Sandbox.builtin_tools()` —
  chain: builtin → `llm_agents.sandbox` MCP middleware :8789 → container
  runner :8788 → subprocess in `sagacient/cutepandas`; one session per
  run, lazily minted under a lock, shared with subagents; DB inside the
  sandbox read-only via preset `PG*` env; caps 120s/600s timeout, 512 KB
  write, 1 MB read); `update_plan` (main lane) and `update_plan_step` +
  `update_plan` (sub kit) — `planning/authorities.py`; step lifecycle
  ticking is RUNNER-side, not a model tool.

## Prompts

Inline Python constants, optionally overridden by DB rows
(`text.ai_prompts`; seed `database/sql/text/15_deep_agent_prompts.sql`;
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
  instructions, end with `final_view` synthesis step; no ops/bookkeeping
  steps.
- `graph/prompts.py`: `STEP_SYSTEM_ADDENDUM` — "you are ONE research
  step"; self-contained finding with numbers/verdict; REPLAN via
  update_plan (only not-yet-run steps; terminate own forks); FORKS via
  background spawn + collect before answering.
- `planning/authorities.py`: `SUBAGENT_PLAN_ADDENDUM` (extend only your
  own fork), `SUBAGENT_SPAWN_ADDENDUM` (sub-sub spawning + gather duty).
- `core/sandbox.py`: `SANDBOX_PROMPT_ADDENDUM` — prefer ONE computational
  script over many MCP queries; psycopg is read-only; use `text_search`
  for Chinese keyword search (raw ILIKE matches nothing).
- Dynamic templates: `{{sec_type}}/{{code}}/{{name}}/{{question}}`
  substituted from `data.scope` (`planning/planner.py::template_args/
  render_template`); user-selected "plan prompts" (prompt_type 2) are
  rendered once per run and passed to every step conversation and fork.
- Prompt management: type-0 row = standing system prompt; type-3 internal
  rows = machinery prompts; editable in the UI prompt view
  (`PUT /api/ai/prompts/:prompt_key`).

## State / persistence

PostgreSQL-first (DDL `database/sql/text/09_deep_agent_states.sql`,
`12_deep_agent_store.sql`, `11_deep_agent_runs_prune.sql`):

- `text.deep_agent_states` — one row per node/task state (thread_id,
  parent_state_id self-FK for fork subgraphs, node, title, status
  pending→in_progress→completed/failed/skipped, ordinal, context refs,
  llm_context_id).
- `text.deep_agent_contexts` — JSONB payloads (plan JSON, step notes,
  subagent results, final-result document).
- `text.deep_agent_identities` — scope (sec_type/code) ↔ thread_id
  reverse index.
- `planning/store.py` — mutation API: `create_thread`, `add_plan_states`,
  `revise_pending_state`, `skip_pending_states`, `tick_step`,
  `create_child_state`/`finish_child_state`, `record_final_result`,
  `finalize_thread`, `purge_thread`, prompt/identity accessors. Every
  write publishes an event.
- LangGraph: `AsyncPostgresSaver` checkpointer (per-superstep snapshots in
  the `langgraph` schema — "interrupt/resume groundwork"; `run_thread`
  always starts a fresh thread, so NO resumability in practice yet,
  stated in the 09 DDL header) + `graph/state_store.py::DeepAgentStore`
  (a real `BaseStore` over `text.deep_agent_store_items`,
  langgraph-checkpoint-postgres 3.x ships no AsyncPostgresStore) holding
  runner-lane node states (`node:plan|step-<id>|execute|finalize|result`)
  and the LLM-context REGISTRY — one uuid context per node execution
  ("ONE NODE, ONE LLM CONTEXT"); a `_closed()` whitelist structurally
  prevents message payloads from ever being stored.
- All persistence fail-soft ("persistence may never fail the summary");
  persisted `ThreadState` carries counts and bounded strings, never
  transcripts (those ride `_RunContext` in-memory only).
- History hygiene: `11_deep_agent_runs_prune.sql` keeps the best closed
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
  1701/1234 (`_core/base.py`, `_core/transport.py`).
- **DB**: everything via `_common.db_commons` plain-SQL rung (asyncpg,
  lazy import for the cudf activation-order rule).

## Observability

- `_core/request_log.py` — one `text.llm_requests` row per LLM call (full
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
