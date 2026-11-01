# DeepSeek agent concepts — Harness (dsh) + Super-agent (studied 2026-11-01)

Sources studied:

- DeepSeek Super-agent architecture write-up
  (https://cellcog.ai/blog/deepseek-super-agent) — summary of the Harness
  team's 2026-10 publication + MIT-licensed prototype
  (`dsh-superagent-prototype`).
- DeepSeek Harness repo README
  (https://github.com/deepseek-ai/deepseek-harness) — released 2026-08-13,
  MIT, developer preview ("there will be compatibility-breaking changes").
- DeepInfra review of dsh
  (https://deepinfra.com/blog/deepseek-harness-review).
- Related: Addy Osmani "Agent Harness Engineering"
  (https://addyosmani.com/blog/agent-harness-engineering) — "the model is
  just one input into a running agent; the rest is the harness — prompts,
  tools, context policies, hooks, sandboxes, subagents."

Note: the LOCAL harness (`llm_agents/deep_agents`) was already modeled on
early DeepSeek Harness notes ("plugin runtime + append-only session log"
per its `__init__.py`). This file records what is NEW beyond that, plus
what is worth adopting here.

## Harness (dsh) — concrete mechanics

- **Everything is a plugin**, built on the Cordis meta-framework
  (spatiotemporal composability paper; TypeScript-first monorepo). A
  plugin can replace even the agent loop itself.
- **Named swap points with shipped implementations**, selected by
  configuration, never by forking: `ctx.sessionPersistence` (JSONL /
  SQLite), `ctx.subprocess` (local / E2B sandbox), `ctx.shell` (bash /
  sandboxed bash / PowerShell), `ctx.subagents` (six providers).
- **Four modes** of one runtime: Standard (full coding agent), Minimal
  (two tools: persistent bash + `str_replace_editor`), Creator (runtime
  inspection for authoring presets), PTC (Code Mode SDK — the model writes
  ONE TypeScript program combining multi-step operations instead of one
  round trip per tool call).
- **Profiles** = ordered stacks of plugin-bundle patch layers with user
  overrides (`$DSH_HOME/profiles/`); `--dump-config` prints the composed
  tree before boot.
- **Headless profile** (`dsh --profile headless "job"`): one fresh
  persisted session, prints the answer, exits — built for CI.
- **Subagent providers** pin and ship their own CLI (Claude Agent SDK /
  Codex), never PATH lookup; auth stays native; only `permissionMode` is
  overridden. `permissionMode: plan` runs the child in PLANNING mode —
  execution denied, ExitPlanMode blocked, the plan returned as the answer.
- **`inheritsParentContext: false` as a designed-for property** — every
  delegation is a cold start; the task text must be self-contained.
- **Per-model routing in one YAML line**: premium model for the outer
  loop, cheap model for mechanical sub-tasks.
- **Compat blocks** (`$DSH_HOME/settings.yaml`) fix OpenAI-compatible
  request-shape mismatches per provider/model (route-level default,
  model-level field-by-field override); `apiKeyEnv` references env vars,
  never stores secrets.
- **Append-only trajectory log by source** — inspect the raw context, not
  a cleaned-up chat rendering.
- **Hooks bridges** (runs existing `hooks.json`), reads `AGENTS.md` /
  `CLAUDE.md`, speaks MCP as a client.
- **Security**: sandbox escape disclosed 2026-09; remediation replaced
  Host-header checks with a one-time token printed at startup. Sandbox
  selectors: Read Only / Workspace Write / Full access — and the
  documented lesson that "Workspace Write" confines writes but NOT reads,
  which surprised users.

## Super-agent (2026-10) — "one router, many workers"

An ALWAYS-ON agent that takes requests from group chat and GitHub, runs
many in parallel, and only interrupts humans when needed. Motivation: a
person follows only ~3–5 active sessions — **human attention, not the
model, caps multitasking**. Goal: minimal human attention, maximum value.

Four design properties:

1. **Continuous** — reachable from chat and GitHub; context carries
   across surfaces (a plan agreed in chat is understood when discussed in
   a PR comment).
2. **Efficient** — accepts new instructions without waiting for the last
   task; runs complex tasks in parallel.
3. **Proactive** — watches events, acts on what is worth handling, stays
   quiet otherwise.
4. **Persistent** — lives in the team's real tools; recovers on its own
   after failures.

Five architecture parts:

1. **Router + workers, two layers.** The router talks to people and hands
   each request to a worker; it deliberately does NOT decompose tasks or
   do work itself (may not even get a shell; a lighter model suffices) —
   kept thin so it never becomes a bottleneck. Workers are isolated, each
   with its own session and directory, on the strongest model, and may
   use subagents.
2. **Events, not prompts.** Chat messages, emails, review comments, CI
   results arrive as triggers; CODE filters first, the model judges only
   what passes. A worker subscribes to its thread, so follow-ups skip the
   router entirely.
3. **Human-tolerable replies.** Long-running tools return immediately so
   the agent keeps responding while work runs; chat replies capped at 100
   words.
4. **Compute / state / execution separation.** The loop runs in the
   cloud, state in a database, and a small daemon runs commands on the
   operator's own machine (which may be only sometimes online).
5. **Watchdog.** A separate timed session checks every worker is making
   real progress; maintainers query it in plain language.

Measured results (synthetic runs: ~300 msgs/run, ~20 work requests, 5
runs each; coding replaced with a time-consuming tool):

- Super-agent p50 12.4 min / p95 15.7 min; single session + subagent tool
  with a neutral prompt NEVER delegated (5/5), p95 29.5 min; even a
  delegation-preferring prompt delegated only 4/5 and was slower →
  **delegation must be structural (a thin router that cannot work), not
  prompt-hoped**.
- Follow-ups routed directly to workers kept completion time but needed
  ~30% more router decisions; cached token reads rose 23.23M → 31.77M →
  follow-up routing matters mostly for attention/context economy.
- Router decision cost fell from 6.4s (~1 trigger/min) to 1.6s (~40
  triggers/min), average batch 2.8 → **batching under load**.

Prototype guardrails worth copying verbatim: acts only when asked or when
a written rule allows; "unrequested" work starts in **observe mode**
(records what it WOULD have done); operators told "do not use it to
produce volume"; a web panel shows only what needs human attention.

Open problems DeepSeek flagged: scaling past one router session; human
workflows when sessions vanish from the screen; context boundaries and
inter-agent communication between instances; security (a compromised
cloud loop endangers every connected machine; credentials must be usable
without entering model context).

## What to adopt in the local harness — mapping

- **Thin router** → `graph/runner.py` router stays a plan-reader; it
  already does not compute. When adding serving features
  (`server.py`), keep the run trigger thin: no task decomposition at the
  HTTP layer.
- **Events, not prompts; code filters first** → `events.py` already
  publishes lifecycle events; any future "proactive" behavior (watch
  pollers, alerts) should filter in Python and only wake the model for
  what passes.
- **Watchdog session** → the repo already watchdogs the daemons
  (`service_heartbeat.py`); a run-level liveness check (is the current
  step making progress vs its budget) is the Super-agent-style addition.
- **Per-model routing** → `core/provider.py` is the swap point; route the
  planner/critic to a stronger model and mechanical steps to a cheaper
  one via config (`service_config/llm_agents/deep_agents/`), not code.
- **PTC / code mode** → the sandbox already gives "one program instead of
  many round trips" for data work; the Harness PTC idea generalizes it to
  tool chains (worth considering if MCP round trips dominate).
- **Headless profile** → `cli.py` is close; a `--json` one-shot is already
  the API bridge. Keep it stable as the CI entry point.
- **`inheritsParentContext: false`** → already the local contract
  (`run_delegation` gets goal + business context only); preserve it.
- **Observe mode** → template for any future unrequested action (memory
  writes, self-improvement prompts): record the proposal, review it,
  then enable.
- **Human-tolerable replies** → the `/run` answer is already a compact
  document; UI-side streaming of findings (not tokens) would extend this.
- **Compute/state/execution separation** → already true (daemon = compute,
  Postgres = state, sandbox container = execution); keep new features in
  the same lanes.
