# External best practices — the study (distilled 2026-11-01)

Sources studied for the local comparison:

- LangChain — Deep Agents Overview
  (https://docs.langchain.com/oss/python/deepagents/overview) and
  "Deep Agents vs LangChain vs LangGraph"
  (https://www.langchain.com/blog/deep-agents-vs-langchain-vs-langgraph)
- Anthropic — "How we built our multi-agent research system"
  (https://www.anthropic.com/engineering/built-multi-agent-research-system)
- Anthropic — "Effective context engineering for AI agents"
  (https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic — "Writing effective tools for AI agents"
  (https://www.anthropic.com/engineering/writing-tools-for-agents)
- Reflection-loop literature: LangChain "Reflection Agents"
  (https://www.langchain.com/blog/reflection-agents), Taskade reflection
  loop, Towards AI "Loop Engineering" — common theme: generator–critic
  with structured criteria, bounded iterations, and EXTERNAL/tool-based
  verification because pure self-review reasons in circles.

## LangChain Deep Agents — the canonical four pillars

An agent harness = LangGraph runtime + four pre-assembled pillars:

1. **Planning** (opt-in since v0.7): `TodoListMiddleware` exposes
   `write_todos`; pending/in_progress/completed persisted in agent state.
   Worth it for long tasks, weaker models, or UIs streaming todo progress.
   Note: the local harness is STRONGER here (DB-persisted living plan +
   structural authority vs a model-trusted todo tool).
2. **Virtual filesystem**: pluggable backends (in-memory, disk, LangGraph
   store, composite, custom; sandbox backends add `execute`); tools `ls`,
   `read_file` (multimodal), `write_file`, `edit_file`, `delete`, `glob`,
   `grep`, `execute`. Purpose is CONTEXT MANAGEMENT — files live beyond
   one context window; declarative permissions (first-match-wins).
3. **Subagents**: `task` tool creates ephemeral subagents returning one
   report; context `mode: "isolated"` (fresh, default) or `"fork"`
   (inherit history); subagents are stateless (no back-chat); async
   subagents run in parallel with launch/check/update/cancel steering.
4. **Memory & skills**: always-loaded AGENTS.md-style memory files,
   updatable by the agent across threads; Agent Skills (SKILL.md per
   directory) with progressive disclosure (frontmatter at startup, body
   on demand).

Also: automatic summarization/offloading of history and large tool
results; automatic prompt caching on static sections (Anthropic/Bedrock);
sandbox backends vs lightweight interpreters (QuickJS) for programmatic
tool calling; human-in-the-loop via `interrupt_on={"tool": True}` LangGraph
interrupts (approve/edit/guide); MCP-as-client; typed streaming
projections including subagent streams.

Guidance on when to use what: managed vs self-hosted; sandboxes when the
agent needs installs/tests/CLIs; interpreters for deterministic
transforms; todo lists for long/model-limited runs; subagents to
quarantine heavy subtasks into compact results — and if you don't need
the built-ins, plain `create_agent` or a custom LangGraph workflow.

## Anthropic multi-agent research system — production lessons

Architecture: orchestrator-worker. Lead agent analyzes the query, plans a
strategy, SAVES THE PLAN TO MEMORY before spawning (context >200k gets
truncated), spawns 3–5 parallel specialized subagents each with its own
context window, subagents make 3+ parallel tool calls, return condensed
findings; a dedicated CitationAgent handles attribution after the loop.

Why it works / when it doesn't: best for breadth-first, parallelizable,
beyond-one-context, complex-tool work. Token usage explains ~80% of
quality variance; multi-agent beat single-agent by 90.2% on internal
evals but uses ~15× chat tokens — only viable for high-value tasks; poor
fit when tasks need shared context or tight inter-agent dependency (most
coding).

Prompt engineering for multi-agent:
- **Teach delegation**: each subtask needs objective, output format, tool
  guidance, clear boundaries — vague instructions cause duplicate or
  misinterpreted work.
- **Scale effort to complexity**: encode explicit rules (simple fact:
  1 agent, 3–10 tool calls; complex: 10+ subagents) to prevent
  overinvestment.
- **Start wide, then narrow** (agents default to overly specific queries).
- **Guide thinking**: extended thinking for lead planning; interleaved
  thinking after tool calls for subagents.
- **Think like your agents**: simulate runs with exact prompts/tools.
- **Let agents improve themselves**: a tool-testing agent that iterated
  on tool descriptions cut task completion time by 40%.
- **Guardrails over rigid rules**: heuristics (source quality, depth vs
  breadth) plus explicit limits (no 50-subagent spawns for trivia).

Tool design: "agent-tool interfaces are as critical as human-computer
interfaces" — self-contained, error-robust, unambiguous; heuristic
guidance (review all tools first, match intent, prefer specialized); bad
descriptions derail agents entirely; MCP makes this harder with unseen,
unevenly described tools.

Evaluation: start with ~20 representative queries immediately (early
tweaks moved success 30%→80%); LLM-as-judge with one call + rubric
(accuracy, citations, completeness, source quality, tool efficiency),
0.0–1.0 + pass/fail worked best; human testing catches what automation
misses (SEO farms over academic PDFs → fixed by prompt heuristics); judge
END STATES, not step-by-step process; evals must tolerate emergent
behavior (small lead-agent changes unpredictably alter subagent behavior).

Production reliability: errors compound — resumable checkpoints instead
of restarts, retry logic, let the agent adapt when told a tool is failing;
full production tracing plus monitoring of decision patterns and
interaction structures (not conversation contents, for privacy); rainbow
deployments (gradual traffic shift, old and new versions coexist) because
in-flight agents must not be disrupted; context management for
long-horizon tasks = summarize completed phases to external memory,
fresh-context subagents with careful handoffs, artifacts stored in FILES
with lightweight references back ("game of telephone" and token-overhead
avoidance).

Overarching: the prototype→production gap is wider than expected; the
last mile becomes most of the journey.

## Context engineering — the compressed doctrine

- Context is a finite attention budget: context rot is a performance
  gradient across all models (n² attention; training on shorter
  sequences). "The smallest set of high-signal tokens that maximize the
  likelihood of the desired outcome."
- System prompts at the right altitude: specific enough to guide,
  flexible enough to stay heuristic; sectioned (background / instructions
  / tool guidance / output format); start minimal on the best model, add
  instructions only for OBSERVED failure modes; few-shot = small set of
  diverse canonical examples, not edge-case lists.
- JIT retrieval vs pre-loading: keep lightweight references (paths,
  stored queries, links), load on demand; metadata itself is a signal
  (hierarchy, naming, timestamps) enabling progressive disclosure;
  recommended pattern is HYBRID — pre-load the few standing artifacts,
  explore the rest just-in-time.
- Long-horizon techniques and when to use them:
  1. **Compaction** — summarize a near-limit conversation, restart with
     the summary (+ recently touched files); keep decisions and
     unresolved bugs, drop redundant tool outputs; tune the compaction
     prompt on traces: maximize recall first, then precision;
     tool-result clearing is the safe light-touch form. → for
     back-and-forth conversations.
  2. **Structured note-taking (agentic memory)** — persistent notes
     outside the window (todo, NOTES.md, memory tool), reloaded later;
     enables coherence across resets. → for milestone-based iterative
     work.
  3. **Sub-agent architectures** — clean windows per subagent, condensed
     1–2k-token summaries back to the coordinator. → for parallel
     research/analysis.

## Verification / critic loops

- Generator–critic: produce → critique against explicit criteria →
  revise, with BOUNDED iterations (usually 1–2; more rarely pays).
- Self-review alone tends to reason in circles (confidently "verifying"
  wrong answers); the fix is EXTERNAL verification — recompute with a
  tool/sandbox, cross-check independent sources or steps, schema/unit
  checks, citation checks.
- Judge final states against the task's success criteria, not the
  process; a critic that can only pass/fail should pass through with the
  risk noted when it cannot verify, and never block delivery forever.
