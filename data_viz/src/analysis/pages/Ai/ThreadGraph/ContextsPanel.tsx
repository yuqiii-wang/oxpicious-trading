/** The context view beneath the graph — the clicked node's, or the
 *  supervisor's by default. */
import { Box, Typography } from "@mui/material";
import type {
  AgentLlmContext,
  AgentLlmUsage,
  AgentNodeState,
  AgentThreadContext,
  AgentThreadState,
} from "@/lib/api-client/aiThreads";
import { formatElapsedS, usageTail } from "./format";
import { ContextBlock } from "./payloadViews";

/** One spawn review of the clicked step's SPAWN RESULTS — the parent
 *  agent's reviewed material (the merge-back edge's payload).
 *  ``childKind`` — the agent_child_kind code (0/undefined spawn,
 *  1 fork — the inherit-context twin; the row label names it). */
export interface SpawnReview {
  stateId: number;
  title: string;
  status: string;
  startedAt: string | null;
  payload: unknown;
  childKind?: number | null;
}

/** ---- the context view beneath the graph: the clicked node's —
 *  or, by DEFAULT (nothing clicked), the supervisor's (the main
 *  path's checker: its runner-lane state, its ONE context and
 *  the kind supervisor-review document). ONE CONTEXT PER NODE
 *  (the runner's design — the node's whole execution, every LLM
 *  request in it, runs as one conversation referenced by id). A SPAWN
 *  node click joins through its own state row instead (spawn children
 *  are not runner-lane nodes — no node_states store item). Renders
 *  nothing when no node is effective or nothing was recorded for it. */
export default function ContextsPanel({
  effectiveNodeId,
  defaultSelection,
  nodeLane,
  nodeLlmContexts,
  nodeUsage,
  usageByContext,
  planContext,
  resultContext,
  supervisorReview,
  spawnReviews,
  spawnState,
  spawnCtx,
  spawnInputPayload,
  spawnAnswerPayload,
}: {
  effectiveNodeId: string | null;
  /** Nothing clicked — the supervisor (or the first default view)
   *  shows, and the caption says so. */
  defaultSelection: boolean;
  nodeLane: AgentNodeState | null;
  nodeLlmContexts: AgentLlmContext[];
  nodeUsage: AgentLlmUsage | null;
  usageByContext: Map<string, AgentLlmUsage>;
  planContext: AgentThreadContext | null;
  resultContext: AgentThreadContext | null;
  supervisorReview: AgentThreadContext | null;
  spawnReviews: SpawnReview[];
  /** The clicked SPAWN node's own state row (null unless a spawn
   *  circle is clicked) — the panel's spawn branch rides it. */
  spawnState?: AgentThreadState | null;
  /** The spawn's ONE llm-context registry row (state_id join). */
  spawnCtx?: AgentLlmContext | null;
  /** The spawn's input context payload — the fresh prompt it was
   *  given (input_context_id). */
  spawnInputPayload?: unknown;
  /** The spawn's answer payload (result_context_id — the subagent
   *  result the parent gathered). */
  spawnAnswerPayload?: unknown;
}) {
  if (
    effectiveNodeId === null ||
    (nodeLane === null &&
      spawnState == null &&
      planContext === null &&
      resultContext === null &&
      supervisorReview === null)
  ) {
    return null;
  }
  return (
    <Box sx={{ mt: 2 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.5 }}
      >
        {defaultSelection
          ? `Contexts — ${effectiveNodeId} by default (no node clicked; click a circle for its own)`
          : "Contexts — one context per node, shared across its LLM requests"}
      </Typography>
      {spawnState != null && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          {spawnState.node} · {spawnState.status} · model{" "}
          {spawnCtx?.model ?? "—"} · {spawnCtx?.n_llm_calls ?? 0} llm calls ·{" "}
          {spawnCtx?.n_tool_calls ?? 0} tool calls
          {spawnState.started_at !== null && spawnState.finished_at !== null
            ? ` · elapsed ${formatElapsedS(
                (new Date(spawnState.finished_at).getTime()
                  - new Date(spawnState.started_at).getTime()) / 1000,
              )}`
            : spawnState.started_at !== null
              ? " · running"
              : ""}
          {usageTail(
            spawnState.llm_context_id !== null
              ? usageByContext.get(spawnState.llm_context_id) ?? null
              : null,
          )}
          {spawnState.error ? ` · ${spawnState.error}` : ""}
        </Typography>
      )}
      {spawnCtx != null && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          ctx {spawnCtx.context_id.slice(0, 6)} · {spawnCtx.n_llm_calls} llm ·{" "}
          {spawnCtx.n_tool_calls} tool requests under this one context
          {usageTail(usageByContext.get(spawnCtx.context_id) ?? null)}
        </Typography>
      )}
      {nodeLane !== null && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          {nodeLane.node} · {nodeLane.status} · model{" "}
          {nodeLane.model ?? "—"} · {nodeLane.n_llm_calls} llm calls ·{" "}
          {nodeLane.n_tool_calls} tool calls
          {nodeLane.n_states > 0 ? ` · ${nodeLane.n_states} tasks` : ""}
          {nodeLane.answer_chars > 0
            ? ` · ${nodeLane.answer_chars} answer chars`
            : ""}
          {nodeLane.elapsed_s !== null
            ? ` · elapsed ${formatElapsedS(nodeLane.elapsed_s)}`
            : ""}
          {usageTail(nodeUsage)}
          {nodeLane.error ? ` · ${nodeLane.error}` : ""}
        </Typography>
      )}
      {nodeLlmContexts.map((c) => (
        <Typography
          key={c.context_id}
          variant="caption"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          ctx {c.context_id.slice(0, 6)} · {c.n_llm_calls} llm ·{" "}
          {c.n_tool_calls} tool requests under this one context
          {usageTail(usageByContext.get(c.context_id) ?? null)}
          {c.checkpoint_id
            ? ` · ckpt ${c.checkpoint_id.slice(0, 8)}`
            : ""}
        </Typography>
      ))}
      {planContext !== null && (
        <Box sx={{ mt: 1 }}>
          <ContextBlock label="plan" payload={planContext.payload} scroll />
        </Box>
      )}
      {resultContext !== null && (
        <Box sx={{ mt: 1 }}>
          <ContextBlock
            label="final result — the Result view's document"
            payload={resultContext.payload}
            scroll
          />
        </Box>
      )}
      {effectiveNodeId === "supervisor" && (
        supervisorReview !== null ? (
          <Box sx={{ mt: 1 }}>
            <ContextBlock
              label="supervisor review — the watchdog's intervention on a root-path failure (error diagnosis · plan/topology verdict)"
              payload={supervisorReview.payload}
              scroll
            />
          </Box>
        ) : (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ display: "block", mt: 1 }}
          >
            {nodeLane?.status === "running"
              ? "watching — intervenes only when a root-path node fails"
              : "idle — no root-path failure (the watchdog made no intervention)"}
          </Typography>
        )
      )}
      {spawnState != null &&
        (spawnInputPayload == null && spawnAnswerPayload == null ? (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ display: "block", mt: 1 }}
          >
            no persisted input/answer context for this
            {spawnState.child_kind === 1 ? " fork" : " spawn"}
          </Typography>
        ) : (
          <Box sx={{ mt: 1, display: "flex", flexDirection: "column", gap: 0.75 }}>
            {spawnInputPayload != null && (
              <ContextBlock
                label={
                  spawnState.child_kind === 1
                    ? "fork input — the fork directive over the inherited conversation"
                    : "spawn input — the fresh prompt the spawn was given"
                }
                payload={spawnInputPayload}
                scroll
              />
            )}
            {spawnAnswerPayload != null && (
              <ContextBlock
                label={spawnState.child_kind === 1 ? "fork answer" : "spawn answer"}
                payload={spawnAnswerPayload}
                scroll
              />
            )}
          </Box>
        ))}
      {spawnReviews.length > 0 && (
        <Box sx={{ mt: 1 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.5 }}
          >
            Spawn results — merged back into this node ·{" "}
            {spawnState != null
              ? "gathered & reviewed by this spawn's conversation (its synthesis rides this spawn's llm requests)"
              : nodeLane?.llm_context_id != null
                ? "gathered & reviewed by this node's conversation (its synthesis rides this node's llm requests)"
                : "delegation — the spawn was this step's engine; its answer became the step's finding"}
          </Typography>
          {spawnReviews.map((spawn) => (
            <Box key={spawn.stateId} sx={{ mb: 0.75 }}>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block" }}
              >
                {spawn.childKind === 1 ? "fork" : "spawn"} · {spawn.status}
                {spawn.startedAt ? ` · ${spawn.startedAt.slice(11, 19)}` : ""}{" "}
                — {spawn.title}
              </Typography>
              {spawn.payload !== null && spawn.payload !== undefined ? (
                <ContextBlock
                  label={spawn.childKind === 1 ? "fork answer" : "spawn answer"}
                  payload={spawn.payload}
                  scroll
                />
              ) : (
                <Typography variant="caption" color="text.disabled">
                  no persisted result context for this
                  {spawn.childKind === 1 ? " fork" : " spawn"}
                </Typography>
              )}
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
}
