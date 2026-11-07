/**
 * SaveTemplateButton — the Thread Graph's plan-template Save action:
 * copy the SETTLED run's persisted plan JSON (the kind 'plan' context
 * payload) into llm.deep_agent_plan_templates with its run-specific
 * variable parts replaced by {{key}} placeholders ({{question}} /
 * {{code}} / {{name}} — the planner template_args vocabulary; the
 * substitution happens server-side, longest-value-first). A later
 * Generate can then start from the template and finish its plan with
 * ZERO planning LLM calls (the plan node materializes the steps with
 * the new run's input args).
 *
 * Shown only for a thread that has a plan to save and is not live
 * (the parent gates both); the click opens a name dialog — saving
 * under an EXISTING name overwrites that template (the name is
 * UNIQUE), which the dialog states. The saved row's detected
 * placeholders confirm what was templated.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Tooltip,
} from "@mui/material";
import { BookmarkAdd } from "@mui/icons-material";
import {
  savePlanTemplate,
  type AiPlanTemplate,
} from "@/lib/api-client/aiPlanTemplates";

export default function SaveTemplateButton({
  threadId,
  onSaved,
}: {
  threadId: string;
  /** Fires on a successful save — the parent refreshes its template
   *  picker so the just-saved template is immediately selectable. */
  onSaved?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<AiPlanTemplate | null>(null);

  useEffect(() => {
    if (!open) return;
    // Fresh dialog state per open (a closed-then-reopened dialog does not
    // resurrect the previous run's confirmation).
    setName("");
    setError(null);
    setSaved(null);
  }, [open]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const template = await savePlanTemplate(threadId, name);
      setSaved(template);
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Tooltip title="Save this run's plan as a reusable template — placeholders replace this run's subject/question, and a later Generate can start from it without LLM planning">
        <Button
          size="small"
          variant="outlined"
          color="primary"
          startIcon={<BookmarkAdd />}
          onClick={() => setOpen(true)}
          sx={{ height: 24, px: 1, fontSize: "0.75rem", textTransform: "none" }}
        >
          Save plan
        </Button>
      </Tooltip>
      <Dialog open={open} onClose={() => saving || setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Save plan template</DialogTitle>
        <DialogContent>
          {saved !== null ? (
            <Stack spacing={1} sx={{ pt: 0.5 }}>
              <Alert severity="success">
                Saved “{saved.name}” — {saved.n_steps} steps templated.
              </Alert>
              {saved.placeholders.length > 0 && (
                <Stack direction="row" spacing={0.5} alignItems="center" useFlexGap flexWrap="wrap">
                  <Chip size="small" variant="outlined" label="placeholders" />
                  {saved.placeholders.map((key) => (
                    <Chip key={key} size="small" label={`{{${key}}}`} sx={{ fontFamily: "monospace" }} />
                  ))}
                </Stack>
              )}
              <Alert severity="info">
                Pick it beside Generate to start a run from this template —
                the plan finishes instantly, no planning LLM calls.
              </Alert>
            </Stack>
          ) : (
            <TextField
              autoFocus
              fullWidth
              size="small"
              label="Template name"
              placeholder="e.g. index ma-spread deep dive"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={saving}
              helperText="Saving under an existing name overwrites that template"
              sx={{ mt: 1 }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim() !== "" && !saving) {
                  void save();
                }
              }}
            />
          )}
          {error !== null && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          {saved !== null ? (
            <Button onClick={() => setOpen(false)}>Close</Button>
          ) : (
            <>
              <Button disabled={saving} onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="contained"
                disabled={saving || name.trim() === ""}
                onClick={() => void save()}
                startIcon={
                  saving ? <CircularProgress size={16} color="inherit" /> : undefined
                }
              >
                Save
              </Button>
            </>
          )}
        </DialogActions>
      </Dialog>
    </>
  );
}
