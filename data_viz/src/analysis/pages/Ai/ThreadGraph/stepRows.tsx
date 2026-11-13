/** The step-type rows of an expanded task row — one recorded inference
 *  request/response pair, one spawn step, one jev decision step, one
 *  StructBERT tokenization step — plus the shared list body that maps
 *  a fetched steps array onto those rows. All row components memoized
 *  off the fetched payload's stable objects: the thread view re-renders
 *  on every live poll tick and WS-event push — without the memo each
 *  tick re-parsed and re-mounted every expanded row's markdown (the
 *  panel's freeze). */
import { memo, useEffect, useState } from "react";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ExpandLess, ExpandMore } from "@mui/icons-material";
import { alpha } from "@mui/material/styles";
import {
  fetchStateAgentSteps,
  type AgentStep,
} from "@/lib/api-client/aiThreads";
import { formatTokensK, INITIAL_REQUEST_ROWS } from "./format";
import { ChatMessage, ContextBlock } from "./payloadViews";

/** One recorded request/response pair of an expanded task row: a meta
 *  line (id · time · model · duration · outcome · tokens) with a
 *  content/raw toggle, then the pair in the picked view — content
 *  (default) lays out the semantic data only (the final assistant
 *  message; the call's NEW request messages, chained rows storing the
 *  shared conversation once), raw shows the stored JSONB payloads as
 *  pretty JSON. Memoized with a stable `req` object from the fetched
 *  payload: the thread view re-renders on every live poll tick and
 *  WS-event push — without the memo each tick re-parsed and re-mounted
 *  every expanded row's markdown (the panel's freeze). */
export const RequestRow = memo(function RequestRow({ req }: { req: AgentStep & { request_id: string; prev_request_id: string | null } }) {
  const [view, setView] = useState<"content" | "raw">("content");
  // Token counts: the request-log columns first, the response payload's
  // usage block as the fallback for rows recorded before the columns.
  const usage = (req.response_payload as
    | { usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }
    | null)?.usage;
  const inTok = req.input_tokens ?? usage?.prompt_tokens ?? null;
  const outTok = req.output_tokens ?? usage?.completion_tokens ?? null;
  const totTok = req.total_tokens ?? usage?.total_tokens ?? null;
  const tokenSummary = [
    inTok !== null ? `${formatTokensK(inTok)} in` : null,
    outTok !== null ? `${formatTokensK(outTok)} out` : null,
    totTok !== null ? `${formatTokensK(totTok)} tok` : null,
  ]
    .filter((part) => part !== null)
    .join(" / ");
  const responseBody = req.response_payload as
    | { choices?: { message?: unknown }[] }
    | null;
  const responseMessage = responseBody?.choices?.[0]?.message ?? null;
  const payload = req.request_payload as Record<string, unknown> | null;
  const messages =
    payload !== null &&
    typeof payload === "object" &&
    Array.isArray(payload.messages)
      ? payload.messages
      : null;
  const incremental = payload?._chain_incremental === true;
  const shared =
    typeof payload?._chain_shared_messages === "number"
      ? payload._chain_shared_messages
      : 0;
  return (
    <Box
      sx={(theme) => ({
        borderLeft: 2,
        borderColor: req.is_failed ? "error.main" : "primary.main",
        borderRadius: 1,
        pl: 1.25,
        pr: 0.75,
        py: 0.5,
        bgcolor: alpha(
          req.is_failed ? theme.palette.error.main : theme.palette.primary.main,
          theme.palette.mode === "dark" ? 0.06 : 0.03,
        ),
        transition: theme.transitions.create("background-color"),
        "&:hover": {
          bgcolor: alpha(
            req.is_failed ? theme.palette.error.main : theme.palette.primary.main,
            theme.palette.mode === "dark" ? 0.12 : 0.06,
          ),
        },
      })}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          flexWrap: "wrap",
        }}
      >
        <Chip
          size="small"
          variant="outlined"
          color={req.is_failed ? "error" : "primary"}
          label="inference"
          sx={{
            height: 16,
            fontSize: "0.6rem",
            fontWeight: 700,
            flexShrink: 0,
            "& .MuiChip-label": { px: 0.5 },
          }}
        />
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ minWidth: 0, flex: "1 1 auto" }}
        >
          <Box component="span" sx={{ fontWeight: 700, color: "text.primary" }}>
            #{req.request_id}
          </Box>{" "}
          · {req.created_at.slice(11, 19)} ·{" "}
          {req.provider !== null ? `${req.provider}/` : ""}
          {req.model ?? "—"} ·{" "}
          {req.duration_ms !== null ? `${(req.duration_ms / 1000).toFixed(1)}s` : "—"} ·{" "}
          {req.is_failed
            ? `failed: ${req.failure_reason ?? "unknown"}`
            : `ok${req.status_code !== null ? ` (${req.status_code})` : ""}`}
          {tokenSummary !== "" ? ` · ${tokenSummary}` : ""}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={view}
          onChange={(_, v: "content" | "raw" | null) => {
            if (v !== null) setView(v);
          }}
        >
          <ToggleButton
            value="content"
            sx={{ px: 0.75, py: 0, fontSize: "0.65rem", textTransform: "none" }}
          >
            content
          </ToggleButton>
          <ToggleButton
            value="raw"
            sx={{ px: 0.75, py: 0, fontSize: "0.65rem", textTransform: "none" }}
          >
            raw
          </ToggleButton>
        </ToggleButtonGroup>
      </Box>
      {view === "raw" ? (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, mt: 0.5 }}>
          <ContextBlock
            label="response — raw payload"
            payload={req.response_payload ?? req.failure_reason ?? "(no response body recorded)"}
            scroll
          />
          {req.request_payload !== null && req.request_payload !== undefined && (
            <ContextBlock
              label="request — raw payload"
              payload={req.request_payload}
              scroll
            />
          )}
        </Box>
      ) : (
        <>
          {responseMessage !== null && typeof responseMessage === "object" ? (
            <Box sx={{ mt: 0.5 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                response
              </Typography>
              <ChatMessage message={responseMessage} />
            </Box>
          ) : (
            <ContextBlock
              label="response"
              payload={req.response_payload ?? req.failure_reason ?? "(no response body recorded)"}
              scroll
            />
          )}
          {messages !== null && (
            <Box sx={{ mt: 0.5 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                request ·{" "}
                {incremental
                  ? `${shared} shared messages stored once · ${messages.length} new`
                  : `${messages.length} messages as sent`}
              </Typography>
              <Box
                sx={{
                  maxHeight: 280,
                  overflowY: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                  mt: 0.25,
                }}
              >
                {messages.map((m, i) => (
                  <ChatMessage key={i} message={m} />
                ))}
              </Box>
            </Box>
          )}
        </>
      )}
    </Box>
  );
});

/** One SPAWN step of an expanded task row: the collapsed sub node —
 *  input = the fresh prompt the spawn was given, output = the sub
 *  node's FINAL output (its answer, or its error when the spawn failed
 *  — the parent collects the error and continues). Tokens are the
 *  child chain's cache-aware rollup and the duration is the wall span
 *  (the parent awaits the child — the step is sequential on the
 *  parent's timeline). CLICKABLE TO EXPAND: the affordance line
 *  ("sub node #N agent steps") lazily loads the child node's OWN
 *  agent steps — the same endpoint keyed on child_state_id, whose
 *  rows are the child's whole chain — and renders them nested inline
 *  (inference/spawn/decision/tokenize rows; the nested spawn rows are
 *  clickable the same way, so a spawn tree unfolds row by row). Fetch
 *  once per row, uncached thereafter like the task list above: a live
 *  run records new child steps. */
export const SpawnStepRow = memo(function SpawnStepRow({
  step,
  threadId,
}: {
  step: AgentStep;
  threadId: string;
}) {
  const [showFull, setShowFull] = useState(false);
  // The child-node expansion: closed by default; the first open fires
  // the one fetch. `notFound` is the endpoint's 404 held as its own
  // flag (storing null in `steps` would read as the loading condition
  // forever — the same trap TaskAgentSteps documents).
  const [open, setOpen] = useState(false);
  const [childSteps, setChildSteps] = useState<AgentStep[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const childStateId = step.child_state_id;
  useEffect(() => {
    if (!open || childSteps !== null || error !== null || notFound) return;
    if (childStateId === null) return;
    let live = true;
    fetchStateAgentSteps(threadId, childStateId)
      .then((d) => {
        if (!live) return;
        if (d === null) setNotFound(true);
        else setChildSteps(d.steps);
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [open, childSteps, error, notFound, threadId, childStateId]);
  const loadingChild = open && childSteps === null && error === null && !notFound;
  const tokenSummary = [
    step.input_tokens !== null ? `${formatTokensK(step.input_tokens)} in` : null,
    step.output_tokens !== null ? `${formatTokensK(step.output_tokens)} out` : null,
    step.total_tokens !== null ? `${formatTokensK(step.total_tokens)} tok` : null,
    step.cache_tokens !== null && step.cache_tokens > 0
      ? `${formatTokensK(step.cache_tokens)} cached`
      : null,
  ]
    .filter((part) => part !== null)
    .join(" / ");
  const out = step.output_text ?? "";
  const truncated = !showFull && out.length > 600;
  return (
    <Box
      sx={(theme) => ({
        borderLeft: 2,
        borderColor: step.is_failed ? "error.main" : "secondary.main",
        borderRadius: 1,
        pl: 1.25,
        pr: 0.75,
        py: 0.5,
        bgcolor: alpha(
          step.is_failed ? theme.palette.error.main : theme.palette.secondary.main,
          theme.palette.mode === "dark" ? 0.06 : 0.03,
        ),
        transition: theme.transitions.create("background-color"),
        "&:hover": {
          bgcolor: alpha(
            step.is_failed ? theme.palette.error.main : theme.palette.secondary.main,
            theme.palette.mode === "dark" ? 0.12 : 0.06,
          ),
        },
      })}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
        <Chip
          size="small"
          variant="outlined"
          color={step.is_failed ? "error" : "secondary"}
          label={step.step_type === 4 ? "fork" : "spawn"}
          sx={{
            height: 16,
            fontSize: "0.6rem",
            fontWeight: 700,
            flexShrink: 0,
            "& .MuiChip-label": { px: 0.5 },
          }}
        />
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 0, flex: "1 1 auto" }}>
          {step.created_at.slice(11, 19)} ·{" "}
          {step.provider !== null ? `${step.provider}/` : ""}
          {step.model ?? "—"} ·{" "}
          {step.duration_ms !== null
            ? `${(step.duration_ms / 1000).toFixed(1)}s`
            : "—"}{" "}
          ·{" "}
          {step.is_failed
            ? "failed"
            : `completed${step.child_status ? ` (${step.child_status})` : ""}`}
          {tokenSummary !== "" ? ` · ${tokenSummary}` : ""}
        </Typography>
      </Box>
      {step.input_text !== null && (
        <Box sx={{ mt: 0.25 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            input — the fresh prompt the spawn was given
          </Typography>
          <ContextBlock label="spawn prompt" payload={step.input_text} />
        </Box>
      )}
      <Box sx={{ mt: 0.25 }}>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          {step.is_failed
            ? "output — the sub node's error"
            : "output — the sub node's final result"}
        </Typography>
        {step.is_failed ? (
          <ContextBlock
            label="spawn error"
            payload={step.output_text ?? step.failure_reason ?? "(no error recorded)"}
          />
        ) : truncated ? (
          <>
            <Typography variant="caption" sx={{ display: "block", whiteSpace: "pre-wrap" }}>
              {out.slice(0, 600)}…
            </Typography>
            <Button
              size="small"
              variant="text"
              sx={{ px: 0.5, py: 0, fontSize: "0.7rem", textTransform: "none" }}
              onClick={() => setShowFull(true)}
            >
              show the full final output
            </Button>
          </>
        ) : (
          <ContextBlock
            label="spawn answer"
            payload={out !== "" ? out : "(no answer recorded)"}
          />
        )}
      </Box>
      {childStateId !== null && (
        <Box sx={{ mt: 0.5 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Button
              size="small"
              variant="text"
              startIcon={
                open ? (
                  <ExpandLess sx={{ fontSize: 14 }} />
                ) : (
                  <ExpandMore sx={{ fontSize: 14 }} />
                )
              }
              onClick={() => setOpen((v) => !v)}
              sx={{ px: 0.5, py: 0, fontSize: "0.7rem", textTransform: "none" }}
            >
              sub node #{childStateId} agent steps
            </Button>
            {loadingChild && <CircularProgress size={12} />}
            {childSteps !== null && (
              <Typography variant="caption" color="text.disabled">
                {childSteps.length} recorded
              </Typography>
            )}
          </Box>
          {open &&
            (error !== null ? (
              <Typography
                variant="caption"
                color="error"
                sx={{ display: "block", pl: 0.5 }}
              >
                sub node agent steps: {error}
              </Typography>
            ) : notFound ? (
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ display: "block", pl: 0.5 }}
              >
                sub node not found under the opened thread id
              </Typography>
            ) : childSteps !== null ? (
              childSteps.length === 0 ? (
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ display: "block", pl: 0.5 }}
                >
                  no agent steps recorded for this sub node
                </Typography>
              ) : (
                <Box sx={{ mt: 0.5, pl: 1.25 }}>
                  <AgentStepList steps={childSteps} threadId={threadId} />
                </Box>
              )
            ) : null)}
        </Box>
      )}
    </Box>
  );
});

/** One DECISION step of an expanded task row (step_type 2): one System
 *  One (jev) read — typed facts in, calibrated verdict out. The read's
 *  payload is NEVER in any LLM conversation (the resumed agent's
 *  instruction carries the action, never the probabilities); this list
 *  is the display surface. content shows the compact facts + verdict
 *  faces; raw shows the typed facts/verdict payloads. */
export const DecisionStepRow = memo(function DecisionStepRow({ step }: { step: AgentStep }) {
  const [view, setView] = useState<"content" | "raw">("content");
  const verdict = step.response_payload as
    | { action?: string; confidence?: number; ok?: boolean; note?: string }
    | null;
  return (
    <Box
      sx={(theme) => ({
        borderLeft: 2,
        borderColor: step.is_failed ? "error.main" : "warning.main",
        borderRadius: 1,
        pl: 1.25,
        pr: 0.75,
        py: 0.5,
        bgcolor: alpha(
          step.is_failed ? theme.palette.error.main : theme.palette.warning.main,
          theme.palette.mode === "dark" ? 0.06 : 0.03,
        ),
        transition: theme.transitions.create("background-color"),
        "&:hover": {
          bgcolor: alpha(
            step.is_failed ? theme.palette.error.main : theme.palette.warning.main,
            theme.palette.mode === "dark" ? 0.12 : 0.06,
          ),
        },
      })}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
        <Chip
          size="small"
          variant="outlined"
          color={step.is_failed ? "error" : "warning"}
          label={`decision · ${step.step_id.replace(/^dec-/, "")}`}
          title="one System One (jev) read — never in LLM context"
          sx={{
            height: 16,
            fontSize: "0.6rem",
            fontWeight: 700,
            flexShrink: 0,
            "& .MuiChip-label": { px: 0.5 },
          }}
        />
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 0, flex: "1 1 auto" }}>
          {step.created_at.slice(11, 19)} · {step.provider ?? "jev"}
          {step.model ? `/${step.model}` : ""} ·{" "}
          {step.duration_ms !== null
            ? `${(step.duration_ms / 1000).toFixed(2)}s`
            : "—"}{" "}
          ·{" "}
          {step.is_failed
            ? `failed: ${step.failure_reason ?? "unknown"}`
            : verdict?.ok
              ? `${verdict.action ?? "?"} p=${verdict.confidence?.toFixed(2) ?? "?"}`
              : `not decisive${verdict?.note ? ` — ${verdict.note}` : ""}`}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={view}
          onChange={(_, v: "content" | "raw" | null) => {
            if (v !== null) setView(v);
          }}
        >
          <ToggleButton
            value="content"
            sx={{ px: 0.75, py: 0, fontSize: "0.65rem", textTransform: "none" }}
          >
            content
          </ToggleButton>
          <ToggleButton
            value="raw"
            sx={{ px: 0.75, py: 0, fontSize: "0.65rem", textTransform: "none" }}
          >
            raw
          </ToggleButton>
        </ToggleButtonGroup>
      </Box>
      {view === "raw" ? (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, mt: 0.5 }}>
          <ContextBlock label="facts — raw payload" payload={step.request_payload ?? "(no facts recorded)"} scroll />
          <ContextBlock label="verdict — raw payload" payload={step.response_payload ?? "(no verdict recorded)"} scroll />
        </Box>
      ) : (
        <Box sx={{ mt: 0.25 }}>
          {step.input_text !== null && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", whiteSpace: "pre-wrap" }}>
              facts — {step.input_text}
            </Typography>
          )}
          <Typography variant="caption" sx={{ display: "block", whiteSpace: "pre-wrap" }}>
            {step.output_text ?? "(no verdict recorded)"}
          </Typography>
        </Box>
      )}
    </Box>
  );
});

/** One TOKENIZATION step of an expanded task row (step_type 3): one
 *  StructBERT segmentation op — search_tsv (a request row's search
 *  vector), context_tags (a context row's tags vector) or query_segment
 *  (the search tool's query pre-segmentation). Off-context by
 *  construction; chars in, search tokens out. */
export const TokenStepRow = memo(function TokenStepRow({ step }: { step: AgentStep }) {
  return (
    <Box
      sx={(theme) => ({
        borderLeft: 2,
        borderColor: step.is_failed ? "error.main" : "success.main",
        borderRadius: 1,
        pl: 1.25,
        pr: 0.75,
        py: 0.5,
        bgcolor: alpha(
          step.is_failed ? theme.palette.error.main : theme.palette.success.main,
          theme.palette.mode === "dark" ? 0.06 : 0.03,
        ),
        transition: theme.transitions.create("background-color"),
        "&:hover": {
          bgcolor: alpha(
            step.is_failed ? theme.palette.error.main : theme.palette.success.main,
            theme.palette.mode === "dark" ? 0.12 : 0.06,
          ),
        },
      })}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
        <Chip
          size="small"
          variant="outlined"
          color={step.is_failed ? "error" : "success"}
          label="tokenize"
          title="one StructBERT segmentation op — never in LLM context"
          sx={{
            height: 16,
            fontSize: "0.6rem",
            fontWeight: 700,
            flexShrink: 0,
            "& .MuiChip-label": { px: 0.5 },
          }}
        />
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 0, flex: "1 1 auto" }}>
          {step.created_at.slice(11, 19)} ·{" "}
          {step.duration_ms !== null
            ? `${step.duration_ms >= 1000 ? `${(step.duration_ms / 1000).toFixed(2)}s` : `${step.duration_ms}ms`}`
            : "batch"}{" "}
          · {step.input_text ?? "—"} · {step.output_text ?? "—"}
          {step.is_failed ? ` · failed: ${step.failure_reason ?? "unknown"}` : ""}
        </Typography>
      </Box>
    </Box>
  );
});

/** The fetched steps of ONE context as rows — the shared list body
 *  under both the task-level "Agent steps" accordion (TaskAgentSteps)
 *  and an expanded spawn step's nested child list. Renders the LATEST
 *  INITIAL_REQUEST_ROWS rows only until expanded — a busy task can
 *  hold 30+ recorded steps, and mounting every row's markdown at once
 *  froze the panel. `threadId` rides down so spawn rows can expand
 *  their child node's own steps in place. */
export function AgentStepList({
  steps,
  threadId,
}: {
  steps: AgentStep[];
  threadId: string;
}) {
  const [showAll, setShowAll] = useState(false);
  return (
    <>
      {steps.length > INITIAL_REQUEST_ROWS && (
        <Button
          size="small"
          variant="text"
          sx={{ px: 0.5, py: 0, fontSize: "0.7rem", textTransform: "none" }}
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll
            ? "collapse to the latest steps"
            : `showing the latest ${INITIAL_REQUEST_ROWS} of ${steps.length} — show all`}
        </Button>
      )}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75, mt: 0.5 }}>
        {(showAll ? steps : steps.slice(-INITIAL_REQUEST_ROWS)).map(
          (step) =>
            step.step_type === 1 || step.step_type === 4 ? (
              // step_type 1 spawn | 4 fork (the inherit-context twin) —
              // the same collapsed-child row; the chip names the kind.
              <SpawnStepRow key={step.step_id} step={step} threadId={threadId} />
            ) : step.step_type === 2 ? (
              <DecisionStepRow key={step.step_id} step={step} />
            ) : step.step_type === 3 ? (
              <TokenStepRow key={step.step_id} step={step} />
            ) : (
              <RequestRow
                key={step.step_id}
                req={{
                  ...step,
                  request_id: step.step_id,
                  prev_request_id: null,
                }}
              />
            ),
        )}
      </Box>
    </>
  );
}
