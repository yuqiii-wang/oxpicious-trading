---
name: deep-agent-blueprint
description: >
  Blueprint of the local deep agent (llm_agents/deep_agents) — the current
  architecture map, distilled best practices (LangChain Deep Agents, Anthropic
  multi-agent research system, context engineering, DeepSeek Harness +
  Super-agent), and the ranked gap roadmap (memory, critic, evals, effort
  scaling, HITL/resume, artifact filesystem). Use for ANY change to
  llm_agents/deep_agents (core loop, planning, graph, delegation, prompts,
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

## The harness today (2026-11-01)

Self-built harness, deliberately NOT LangChain `deepagents`, modeled on
Claude Code / DeepSeek Harness / WorkBuddy (design notes in
`llm_agents/deep_agents/__init__.py`). Three layers, strict direction:

- `core/` — the runtime: one message list, one OpenAI-style tool-calling
  loop per step (`core/loop.py::DeepAgent.run`), sibling tool calls via
  `asyncio.gather`, MCP client (`core/mcp.py`), docker py-sandbox client
  (`core/sandbox.py`), ZhiPu provider (`core/provider.py`, glm-5.3-flash).
- `planning/` — the living-plan domain: plan-JSON contract with
  propose→validate→correct repair rounds (`planning/planner.py`),
  `PlanAuthority` permission model (`planning/authorities.py`), PG
  persistence (`planning/store.py` → `text.deep_agent_states/contexts/
  identities`).
- `graph/` — LangGraph StateGraph orchestration (`graph/runner.py`):
  `plan → step* → finalize → result` plus a degraded one-conversation
  execute lane when planning fails; per-hop max_steps clamped to
  `max(4, min(run_max_steps, 20))`; recursion_limit 80.

Delegation: sync `spawn_subagent` + background forks with an explicit
`collect_subagents`, an answer gate for uncollected forks, semaphore cap 4,
`MAX_SPAWN_DEPTH = 2`, parent-owned supervision hooks
(`core/delegation.py`, `graph/hooks.py`). Serving: FastAPI daemon :8766
(`deep_agents/server.py`, `/run /stop /active /ws`) bridged to the UI by
`data_viz/api/services/ai-summary.service.ts`. Observability: per-LLM-call
rows in `text.llm_requests` with usage tokens + chain linkage
(`_core/request_log.py`), bounded event bus (`deep_agents/events.py`),
per-node metrics (`graph/state_store.py::NodeRunWriter`).

2026-11-01 additions: context rows are chained to their node's ONE LLM
context (`text.deep_agent_contexts.node/state_id/llm_context_id` — the
same uuid as `llm_requests.chain_id`, so payloads link the requests
that produced them), and every request row carries a GIN-indexed
`search_tsv` (A = incremental input, B = output; StructBERT-segmented,
writer-thread tokenization, `backfill_search_tsv_async` heals NULLs).
On top of both sits the THREAD-INTERNAL SEARCH — a
`search_thread_context` builtin in both plan kits
(`planning/thread_search.py`): any-level agent keyword-searches its own
thread's recorded traffic, scoped by chain membership, healing its
thread's unindexed rows before searching.

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
   (`graph/prompts.py::findings_block`, per-finding 1200 chars, block
   12000, aged down to title lines). Forks inherit only run goal + business
   context, never the parent transcript.
3. **Error-as-state + always-answer.** A failed step settles its row and
   the chain advances; persistence is fail-soft; the degraded execute lane
   guarantees an answer even when planning never persisted. The run's
   answer is the LAST COMPLETED step's finding.
4. **Answer gate for uncollected forks** + orphan-fork cancellation in a
   `finally` — the exact failure mode Anthropic documents.
5. **Context hygiene as hard policy.** Transcripts/messages are never
   persisted (a `_closed()` whitelist prevents it); contexts are referenced
   by uuid; "ONE NODE, ONE LLM CONTEXT".
6. **Compute as a first-class tool.** The docker py-sandbox (read-only DB,
   caps) is the substrate for verification work — use it when building the
   critic (see roadmap).
7. **No-transcript observability.** `text.llm_requests` + node metrics give
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

- `text.deep_agent_memories` (SQL-first DDL): scoped rows (sec_type/code
  via the existing scope pattern in `text.deep_agent_identities`), kind
  column as SMALLINT code (fact / preference / pitfall / reusable-query),
  body, source thread_id, confidence, timestamps. Purge/merge policy like
  `11_deep_agent_runs_prune.sql`.
- Write at `finalize` (`planning/store.py`): distill durable facts from the
  run's `final-result` context — model-summarized, capped, deduped.
- Inject in `planning/planner.py` template args and the step user message
  (`graph/prompts.py::step_user_message`) — pre-load the small standing
  set, keep bulk memory JIT-retrievable.
- Curation is structural: only finalized threads with a completed
  `final_view` step may write memories.

### P1 — critic / verification node

Today the answer is just the last completed step's finding — if synthesis
fails, the answer silently degrades. Build:

- One bounded critic node between `step` and `finalize` in
  `graph/runner.py` (or a critic addendum on the `final_view` step): fresh
  conversation, sees plan + top findings, not transcripts.
- External checks over self-review: sandbox-recompute numbers cited in
  findings; cross-check conflicting step claims; verify web-sourced claims
  have citations. Bounded: one repair round, then pass-through with the
  residual risk noted.
- Critic verdict as SMALLINT code in a context row; veto routes back to a
  repair step once (error-as-state, never raise).

### P1 — evaluation harness

The raw material already exists — full replay from `text.llm_requests`
chains + `deep_agent_states`. Build:

- ~20-question regression set across sec_types in `temp_scripts/` or a
  small DB table; runner replays each question through `run_thread` with
  fixed seeds/limits.
- LLM-as-judge with a fixed rubric (numbers correct / completeness /
  tool efficiency / no hallucinated codes), scored 0–1, stored per run;
  trend line over the prune-job survivors.
- Gate: any harness change (prompts, limits, routing) that drops the mean
  score needs a stated reason.

### P2 — effort scaling + token budget

Fixed limits today (≤12 steps, ≤6 forks, 4 concurrent); no complexity
scaling, no run token ceiling. Build: complexity tier in plan-JSON
(planner already classifies), tier sets step/fork/concurrency budgets;
enforce a per-run token ceiling at the `_core/request_log.py` choke point
(usage already logged) — soft-warn at 80%, hard-stop by refusing further
provider calls.

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
- Per-thread cost rollup: pricing table × `text.llm_requests` usage, shown
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
  the DB-editable prompt rows (`text.ai_prompts`) as a reviewable diff —
  Anthropic's highest-ROI loop (40% time cut from model-improved tool
  descriptions).

## House rules when implementing

- ANY code change → `oxpicious-dev-conventions` first; sandbox-side changes
  → `py-sandbox-env`; new DDL → `db-sql-write` + `db-smallint-type-codes`.
- New daemons/middleware register in `_common/service_heartbeat.py::
  MONITORED_SERVICES` (name = module path = spawn contract).
- Verify by running a scoped question end-to-end (`--sec-type index`
  scope) and checking `text.llm_requests` chains + the UI Thread Graph, not
  just the code; throwaway probes in `temp_scripts/`.
- Preserve the layering rule: `core` knows nothing of planning/graph;
  `planning` knows nothing of graph; only `graph` composes. No cudf/pandas
  in the package.
- The pure modules (`planner`, `plan_json`) unit-test against a duck-typed
  `chat_complete` stub — keep new domain modules equally stub-testable.

## Keeping this skill current

- When a roadmap item lands, move it from the gap list into "The harness
  today" with its file paths, and date it like the rest of this repo
  (`2026-xx-xx`).
- Before major harness work, re-check the external landscape (LangChain
  Deep Agents changelog, Anthropic engineering blog, DeepSeek Harness
  releases — the Super-agent was published 2026-10 and is explicitly
  research code with breaking changes expected) and fold new findings into
  `references/`.
