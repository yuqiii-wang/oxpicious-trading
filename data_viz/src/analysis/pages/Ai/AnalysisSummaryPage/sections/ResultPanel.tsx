/** The central result panel — the toolbar (alerts, the LangGraph/Result
 *  toggle or the prompt mode's Edit/Save pair, the plan-template pick +
 *  Generate/Stop) over the body switch (prompt content / thread graph /
 *  result document / placeholder). */
import { Alert, Box, Button, CircularProgress, IconButton, MenuItem, Paper, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from "@mui/material";
import { Cancel, Close, Edit, PlayArrow, Save, Stop } from "@mui/icons-material";
import {
  promptIdentity,
  type AiPrompt,
  type PromptLabelSets,
} from "@/lib/api-client/aiSummary";
import type { AiPlanTemplate } from "@/lib/api-client/aiPlanTemplates";
import type { AgentThreadDetail } from "@/lib/api-client/aiThreads";
import MarkdownView from "@/shared/markdown/MarkdownView";
import { SUMMARY_PLACEHOLDER_MD } from "../../constants";
import PromptContent from "../../PromptContent";
import PromptLabels from "../../PromptLabels";
import ThreadGraphView from "../../ThreadGraph";
import type { FinalResultPayload } from "../useSummaryHistory";

export default function ResultPanel({
  errorMsg,
  onCloseError,
  noticeMsg,
  onCloseNotice,
  openPrompt,
  openBasePrompt,
  editingPrompt,
  onEditingChange,
  savingPrompt,
  draftInstruction,
  onDraftChange,
  onStartPromptEdit,
  onSavePromptEdit,
  resultView,
  onResultViewChange,
  planTemplates,
  planTemplateId,
  onPlanTemplateChange,
  onRemovePlanTemplate,
  generating,
  runLive,
  onStopGeneration,
  onOpenGenerateDialog,
  sessionLabels,
  labelSuggestions,
  labelsFocusSignal,
  onApplyLabelChange,
  graphThreadId,
  onSettled,
  graphSignal,
  onPlanTemplateSaved,
  onGraphStop,
  resultThreadId,
  resultDetail,
  resultLoading,
  finalResultPayload,
  summaryMd,
}: {
  errorMsg: string | null;
  onCloseError: () => void;
  noticeMsg: string | null;
  onCloseNotice: () => void;
  openPrompt: AiPrompt | null;
  openBasePrompt: AiPrompt | null;
  editingPrompt: boolean;
  onEditingChange: (editing: boolean) => void;
  savingPrompt: boolean;
  draftInstruction: string;
  onDraftChange: (draft: string) => void;
  onStartPromptEdit: () => void;
  onSavePromptEdit: () => void;
  resultView: "langgraph" | "result";
  onResultViewChange: (view: "langgraph" | "result") => void;
  planTemplates: AiPlanTemplate[];
  planTemplateId: number | null;
  onPlanTemplateChange: (id: number | null) => void;
  onRemovePlanTemplate: (templateId: number) => void;
  generating: boolean;
  runLive: boolean;
  onStopGeneration: () => void;
  onOpenGenerateDialog: () => void;
  sessionLabels: Record<string, Partial<PromptLabelSets>>;
  labelSuggestions: PromptLabelSets;
  labelsFocusSignal: number;
  onApplyLabelChange: (
    identity: string,
    group: keyof PromptLabelSets,
    label: string,
    add: boolean,
    permanent: boolean,
  ) => void;
  graphThreadId: string | null;
  onSettled: () => void;
  graphSignal: number;
  onPlanTemplateSaved: () => void;
  onGraphStop: (superThreadId: string) => Promise<void>;
  resultThreadId: string | null;
  resultDetail: AgentThreadDetail | null;
  resultLoading: boolean;
  finalResultPayload: FinalResultPayload | null;
  summaryMd: string | null;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 3, minWidth: 0, flexGrow: 1 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
          mb: 2,
        }}
      >
        <Box>
          {errorMsg !== null && (
            <Alert severity="error" onClose={onCloseError}>
              {errorMsg}
            </Alert>
          )}
          {noticeMsg !== null && (
            <Alert severity="info" onClose={onCloseNotice}>
              {noticeMsg}
            </Alert>
          )}
        </Box>
        {/* LangGraph / Result toggle — the merged run-observability
            view vs the generated document (prompt mode hides it). */}
        {!openPrompt && (
          <ToggleButtonGroup
            exclusive
            size="small"
            value={resultView}
            onChange={(_, v) => v !== null && onResultViewChange(v)}
            sx={{ flexShrink: 0 }}
          >
            <ToggleButton value="langgraph">LangGraph</ToggleButton>
            <ToggleButton value="result">Result</ToggleButton>
          </ToggleButtonGroup>
        )}
        {openPrompt ? (
          // Prompt mode — the content view's actions replace the
          // Generate button: Edit to start, Save (version +1) / Cancel
          // while editing.
          editingPrompt ? (
            <Box sx={{ display: "flex", gap: 1, flexShrink: 0 }}>
              <Button
                startIcon={<Cancel />}
                disabled={savingPrompt}
                onClick={() => onEditingChange(false)}
              >
                Cancel
              </Button>
              <Button
                variant="contained"
                startIcon={
                  savingPrompt ? (
                    <CircularProgress size={16} color="inherit" />
                  ) : (
                    <Save />
                  )
                }
                disabled={
                  savingPrompt ||
                  draftInstruction.trim() === "" ||
                  draftInstruction.trim() === openPrompt.instruction
                }
                onClick={onSavePromptEdit}
              >
                Save
              </Button>
            </Box>
          ) : (
            <Button
              variant="outlined"
              startIcon={<Edit />}
              onClick={onStartPromptEdit}
              sx={{ flexShrink: 0 }}
            >
              Edit
            </Button>
          )
        ) : (
          <Box
            sx={{
              display: "flex",
              gap: 1,
              alignItems: "center",
              flexShrink: 0,
              flexWrap: "wrap",
            }}
          >
            {/* Plan template pick — a SAVED plan (the Thread Graph's
                Save button) to start from instead of the LLM planning
                session: the plan materializes instantly with this
                run's scope/question args filling the template's
                placeholders. */}
            <Tooltip
              title={
                planTemplates.length === 0
                  ? "No saved plan templates yet — settle a run, then use its thread graph's “Save plan” button"
                  : "Start from a saved plan template — the plan finishes instantly (placeholders filled with this run's scope), skipping LLM planning"
              }
            >
              <TextField
                select
                size="small"
                label="Plan template"
                value={planTemplateId ?? ""}
                disabled={generating || planTemplates.length === 0}
                onChange={(e) =>
                  onPlanTemplateChange(
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
                sx={{ minWidth: 210 }}
              >
                <MenuItem value="">LLM planning (default)</MenuItem>
                {planTemplates.map((t) => (
                  <MenuItem
                    key={t.template_id}
                    value={t.template_id}
                    sx={{
                      "& .tpl-delete": { visibility: "hidden" },
                      "&:hover .tpl-delete": { visibility: "visible" },
                    }}
                  >
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        width: "100%",
                        minWidth: 0,
                      }}
                    >
                      <Typography variant="inherit" noWrap sx={{ flex: 1, minWidth: 0 }}>
                        {t.name} · {t.n_steps} steps
                        {t.placeholders.length > 0
                          ? ` · ${t.placeholders
                              .map((p) => `{{${p}}}`)
                              .join(" ")}`
                          : ""}
                      </Typography>
                      <IconButton
                        className="tpl-delete"
                        size="small"
                        edge="end"
                        aria-label="Delete plan template"
                        disabled={generating}
                        onClick={(e) => {
                          e.stopPropagation();
                          onRemovePlanTemplate(t.template_id);
                        }}
                      >
                        <Close fontSize="small" />
                      </IconButton>
                    </Box>
                  </MenuItem>
                ))}
              </TextField>
            </Tooltip>
            <Box>
              <Button
                variant="contained"
                color={runLive ? "error" : "primary"}
                startIcon={runLive ? <Stop /> : <PlayArrow />}
                // Always enabled — Generate when idle, Stop while a
                // run is live. The forced-table scope is an OPTIONAL
                // dialog knob (empty = no limitation), never a
                // precondition (gating on it dead-locked the button:
                // it is only populated by the dialog this button
                // opens); and a live run's Stop must stay clickable
                // (the daemon's cancel handles both the known-thread
                // and the thread-less forms).
                onClick={runLive ? onStopGeneration : onOpenGenerateDialog}
              >
                {runLive ? "Stop" : "Generate"}
              </Button>
            </Box>
          </Box>
        )}
      </Box>
      {openPrompt ? (
        <PromptContent
          prompt={openPrompt}
          editing={editingPrompt}
          draft={draftInstruction}
          onDraftChange={onDraftChange}
          labelsSlot={
            openBasePrompt ? (
              <PromptLabels
                baseline={{
                  bound: openBasePrompt.bound_labels,
                  optional: openBasePrompt.optional_labels,
                }}
                effective={{
                  bound: openPrompt.bound_labels,
                  optional: openPrompt.optional_labels,
                }}
                sessionModified={
                  sessionLabels[promptIdentity(openPrompt)] !== undefined
                }
                suggestions={labelSuggestions}
                focusSignal={labelsFocusSignal}
                onCommit={(group, label, add, permanent) =>
                  onApplyLabelChange(
                    promptIdentity(openPrompt),
                    group,
                    label,
                    add,
                    permanent,
                  )
                }
              />
            ) : undefined
          }
        />
      ) : resultView === "langgraph" ? (
        <ThreadGraphView
          threadId={graphThreadId}
          expectLive={generating}
          onSettled={onSettled}
          refreshSignal={graphSignal}
          onPlanTemplateSaved={onPlanTemplateSaved}
          onStop={onGraphStop}
        />
      ) : finalResultPayload !== null ? (
        finalResultPayload.error !== undefined ? (
          <Alert severity="error">
            This run recorded a failure instead of a result:{" "}
            {finalResultPayload.error}
          </Alert>
        ) : (
          <MarkdownView source={finalResultPayload.answer ?? ""} />
        )
      ) : resultThreadId !== null ? (
        <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
          {resultDetail === null && (resultLoading || generating)
            ? "Waiting for the run's result…"
            : "This run left no recorded result — it was stopped before the agent finished, or it predates result recording."}
        </Typography>
      ) : summaryMd !== null ? (
        <MarkdownView source={summaryMd} />
      ) : (
        <MarkdownView source={SUMMARY_PLACEHOLDER_MD} />
      )}
    </Paper>
  );
}
