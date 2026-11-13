/** The run-limits field definitions — pure data (the groups and their
 *  bounds) plus the TextField draft helper. */
import type { DeepAgentLimits } from "@/lib/api-client/config";

/** One editable limit: the payload key, its bound and its one-line
 *  help (the label's tooltip). Ranges mirror the route's validation
 *  table (data_viz/api/routes/config/deep-agent.ts) and limits.py::from_doc. */
export interface LimitFieldDef {
  key: NumericLimitKey;
  label: string;
  min: number;
  max: number | null;
  /** null = unbounded (empty input); only subagentTimeoutSec today. */
  nullable?: boolean;
  help: string;
}

export type NumericLimitKey = {
  [K in keyof DeepAgentLimits]: DeepAgentLimits[K] extends number | null
    ? K
    : never;
}[keyof DeepAgentLimits];

/** Display order — grouped the way a run spends: the conversation
 *  budget, the plan tree that multiplies it, the subagent fan-out,
 *  the per-request size caps, the prompt/payload budgets. */
export const LIMIT_GROUPS: Array<{ title: string; fields: LimitFieldDef[] }> = [
  {
    title: "Conversation steps",
    fields: [
      {
        key: "maxConversationSteps",
        label: "Max steps / conversation",
        min: 1,
        max: 1000,
        help: "The per-conversation hop budget (one hop = one LLM turn that may batch tool calls). The default the bridge and server send for a run.",
      },
      {
        key: "minConversationSteps",
        label: "Clamp floor",
        min: 1,
        max: 64,
        help: "Whatever a run asks for, one conversation keeps at least this many hops.",
      },
      {
        key: "maxStepsCap",
        label: "Clamp ceiling",
        min: 1,
        max: 1000,
        help: "Keeps one conversation from eating the whole run. A delegated spawn gets +8 headroom under this same ceiling.",
      },
      {
        key: "recursionLimit",
        label: "Recursion limit",
        min: 25,
        max: 100000,
        help: "The LangGraph superstep bound for one graph invocation: plan + step hops + replans + finalize + result.",
      },
      {
        key: "runTimeoutSec",
        label: "Run timeout (s)",
        min: 60,
        max: null,
        help: "The run's TOTAL wall-clock budget (default 5h = 18000s): the step lane checks it every hop and routes to finalize at the budget — a clean settle instead of grinding forever. The bridge's and the browser's deadlines sit at the same value.",
      },
      {
        key: "llmRequestTimeoutSec",
        label: "LLM request timeout (s)",
        min: 5,
        max: null,
        help: "The per-LLM-request wall-clock budget (default 300s): the provider's retry ladder terminates at the budget. An overrun means the input is too heavy for one call — the harness terminates it and refactors the input into smaller tasks (the planner's slimmed init, the spawn pre-planner), never a same-payload retry. This bounds ONE individual LLM request, not a node: a step node runs many requests across its tool turns and may take far longer than this value — nodes are bounded by the conversation step budget and the run by run timeout instead.",
      },
    ],
  },
  {
    title: "Plan tree",
    fields: [
      {
        key: "maxPlanNodes",
        label: "Max planned steps",
        min: 1,
        max: 200,
        help: "How many steps the planner's plan may hold (extra steps are truncated). Quoted in the planning prompt.",
      },
      {
        key: "maxDelegatedSteps",
        label: "Max delegated steps",
        min: 0,
        max: 200,
        help: "How many planned steps may carry a spawn marker (each spawn is a fresh subagent conversation).",
      },
      {
        key: "planRepairRounds",
        label: "Plan repair rounds",
        min: 0,
        max: 10,
        help: "The planning session's propose → correct round-trips per batch before the run degrades to the no-plan execute lane.",
      },
      {
        key: "planProposalBatches",
        label: "Plan proposal batches",
        min: 1,
        max: 24,
        help: "The batched proposal protocol's round cap: the first call proposes the first few steps, later rounds propose the next batch over the nodes so far — each a small LLM call that persists immediately, so planning never hangs on one giant reply.",
      },
      {
        key: "maxTotalPlanSteps",
        label: "Max total plan steps",
        min: 1,
        max: 1000,
        help: "The replan guard: update_plan / external adds may not grow the tree past this.",
      },
      {
        key: "maxSpawnChildren",
        label: "Max spawn children",
        min: 0,
        max: 200,
        help: "How many sub-nodes one spawn may add under itself.",
      },
    ],
  },
  {
    title: "Subagents",
    fields: [
      {
        key: "maxParallelDelegatedSteps",
        label: "Spawn wave width",
        min: 1,
        max: 64,
        help: "How many delegated steps of one hop run concurrently (the rest queue behind the slots).",
      },
      {
        key: "maxConcurrentSubagents",
        label: "Concurrent subagents / agent",
        min: 1,
        max: 64,
        help: "The per-agent spawn semaphore — the parent's own fan-out cap (propagates to every child).",
      },
      {
        key: "maxSpawnDepth",
        label: "Spawn depth",
        min: 0,
        max: 8,
        help: "The spawn-chain depth cap: an agent AT the depth carries no spawn tool (0 disables delegation).",
      },
      {
        key: "subagentTimeoutSec",
        label: "Subagent timeout (s)",
        min: 1,
        max: null,
        nullable: true,
        help: "Wall clock per spawned child; empty = unbounded (the historical behavior).",
      },
    ],
  },
  {
    title: "LLM request budget",
    fields: [
      {
        key: "maxOutputTokens",
        label: "Max output tokens / request",
        min: 256,
        max: null,
        help: "The per-request output ceiling — THE runaway guard. Loose by design: a finding is ~2-4k tokens.",
      },
      {
        key: "maxOutputRepairs",
        label: "Output repair rounds",
        min: 0,
        max: 5,
        help: "Bounded correction turns when a conversation's final answer fails structural validation.",
      },
    ],
  },
  {
    title: "Findings bridge",
    fields: [
      {
        key: "findingMaxChars",
        label: "Per-finding chars",
        min: 50,
        max: null,
        help: "One step's finding is capped per line before it joins the bridge.",
      },
      {
        key: "findingsBlockMaxChars",
        label: "Findings block chars",
        min: 100,
        max: null,
        help: "The whole block the next step sees; older findings age to title lines past it.",
      },
    ],
  },
  {
    title: "Payload guard",
    fields: [
      {
        key: "payloadEntryMaxChars",
        label: "Entry guard chars",
        min: 200,
        max: null,
        help: "A structured-context subtree whose serialized size passes this ships as a _preview envelope instead of full content (shape, per-column stats, head/mid/tail batches, how to read).",
      },
      {
        key: "payloadGuardChars",
        label: "Whole-block gate chars",
        min: 0,
        max: null,
        help: "After per-entry guarding, the whole data block is trimmed (largest entries first) down to this gate — a budget against many medium entries.",
      },
      {
        key: "payloadPreviewChars",
        label: "Preview chars / value",
        min: 100,
        max: null,
        help: "How much of one elided value's content the preview keeps.",
      },
      {
        key: "payloadPreviewItems",
        label: "Preview items / container",
        min: 1,
        max: 50,
        help: "How many elements/entries an elided array or object previews.",
      },
      {
        key: "toolResultMaxChars",
        label: "Tool result chars",
        min: 500,
        max: null,
        help: "One tool result's text, head+tail capped before it enters the transcript — a wide db_run_query dump (500 rows is a row cap, not a byte cap) would otherwise ride every later turn of the hop.",
      },
    ],
  },
  {
    title: "Prompt loading",
    fields: [
      {
        key: "promptEagerRows",
        label: "Eager rows",
        min: 0,
        max: 100,
        help: "A skills selection at or under this many rows loads fully without the planner's verdict.",
      },
      {
        key: "promptEagerChars",
        label: "Eager chars",
        min: 0,
        max: null,
        help: "…or a matched set whose full texts fit this budget also loads eagerly.",
      },
    ],
  },
  {
    title: "Supervisor repair lane",
    fields: [
      {
        key: "supervisorPollIntervalSec",
        label: "Poll interval (s)",
        min: 2,
        max: 60,
        help: "How often the supervisor session (the backend poller beside the graph) re-reads the settled node states while a run executes.",
      },
      {
        key: "supervisorRepairGraceSec",
        label: "Repair window (s)",
        min: 0,
        max: 600,
        help: "On a failed step the lane pauses this long for the supervisor's verdict (retry / ignore). 0 disables repair — the lane never waits. Must cover the intervention call plus any retry throttle.",
      },
      {
        key: "supervisorMaxNodeRetries",
        label: "Retries / node",
        min: 0,
        max: 3,
        help: "How many times ONE failed step may be re-run on the supervisor's retry verdict.",
      },
      {
        key: "supervisorMaxRepairsPerRun",
        label: "Repairs / run",
        min: 0,
        max: 6,
        help: "The run-wide cap on re-opens — past it every further failure falls through to no-action.",
      },
      {
        key: "supervisorRetryThrottleSec",
        label: "Rate-limit cooldown (s)",
        min: 0,
        max: 300,
        help: "A retry on a rate-limit-class failure waits this long INSIDE the repair window before the hop re-runs — an immediate re-run would re-hit the hot account.",
      },
    ],
  },
];

export const ALL_FIELDS = LIMIT_GROUPS.flatMap((g) => g.fields);

/** The numeric entry as input strings (the TextField draft). */
export function draftOf(limits: DeepAgentLimits): Record<NumericLimitKey, string> {
  const draft = {} as Record<NumericLimitKey, string>;
  for (const f of ALL_FIELDS) {
    const value = limits[f.key];
    draft[f.key] = value === null ? "" : String(value);
  }
  return draft;
}
