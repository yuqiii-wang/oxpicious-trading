/**
 * PromptLabels — the prompt view's Labels section: the row's two label
 * arrays as removable chip groups — bound_labels (the force-load
 * keywords: a run question containing one force-loads the prompt
 * alongside the picked rows) and optional_labels (the search vocabulary
 * that makes the prompt findable). Each group's add control is a
 * free-solo Autocomplete: its option list is the registry's existing
 * label vocabulary (the scope-bound guides' seeded phrases — pick one to
 * stay consistent), and typed text commits as a CUSTOM label, so both
 * scope reuse and free input work the same way.
 *
 * Every committed change (add or remove, chip x or Autocomplete entry)
 * first asks how far it should live (the dialog): "Save permanently"
 * PUTs the row's full effective arrays to
 * /api/ai/prompts/:key/labels, while "Just this run" keeps the change in
 * the page's session state — dashed chips mark session additions,
 * struck-through chips the session removals, and the whole delta is
 * FORGOTTEN when the page unmounts (leaving the UI page). Run-scoped
 * changes also ride the next Generate request as promptLabels overrides.
 */
import { useEffect, useRef, useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from "@mui/material";
import type { PromptLabelSets } from "@/lib/api-client/aiSummary";

/** One label group's identity — the row's two arrays. */
type LabelGroup = keyof PromptLabelSets;

const GROUP_CAPTIONS: Record<LabelGroup, string> = {
  bound: "Bound — force-load on a question match",
  optional: "Optional — search vocabulary",
};

interface PromptLabelsProps {
  /** The row's PERSISTED arrays (llm.ai_prompts) — the delta baseline. */
  baseline: PromptLabelSets;
  /** The row's EFFECTIVE arrays (persisted + the session delta). */
  effective: PromptLabelSets;
  /** True when the session carries any change for this prompt — shows
   *  the "this run" marker on the section header. */
  sessionModified: boolean;
  /** The registry-wide label vocabulary per group (the suggestions). */
  suggestions: PromptLabelSets;
  /** A committed change: add/remove `label` in `group`, persisted when
   *  `permanent` else session-scoped. The dialog calls it once confirmed. */
  onCommit: (
    group: LabelGroup,
    label: string,
    add: boolean,
    permanent: boolean,
  ) => void;
  /** Bumped when the picker row's "+" button opens the prompt — the
   *  section scrolls into view and the bound group's input focuses. */
  focusSignal?: number;
}

/** One group's chips + its add control. Kept labels render solid, the
 *  session additions dashed, the session removals struck-through (their
 *  x re-adds — the dialog's choice decides how far the restore lives). */
function LabelGroupRow({
  group,
  baseline,
  effective,
  suggestions,
  onIntent,
  inputRef,
}: {
  group: LabelGroup;
  baseline: string[];
  effective: string[];
  suggestions: string[];
  onIntent: (group: LabelGroup, label: string, add: boolean) => void;
  /** The bound group exposes its input for the "+" button's focus. */
  inputRef?: React.RefObject<HTMLInputElement | null>;
}) {
  const [input, setInput] = useState("");
  const kept = baseline.filter((l) => effective.includes(l));
  const added = effective.filter((l) => !baseline.includes(l));
  const removed = baseline.filter((l) => !effective.includes(l));
  const has = (label: string) => effective.includes(label);

  const commitInput = () => {
    const value = input.trim();
    if (value === "" || has(value)) return;
    setInput("");
    onIntent(group, value, true);
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
      <Typography variant="caption" color="text.secondary">
        {GROUP_CAPTIONS[group]}
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, alignItems: "center" }}>
        {[...kept, ...added, ...removed].map((label) => {
          const isAdded = added.includes(label);
          const isRemoved = removed.includes(label);
          return (
            <Chip
              key={label}
              label={label}
              size="small"
              color={group === "bound" ? "primary" : "default"}
              variant="outlined"
              onDelete={() => onIntent(group, label, isRemoved)}
              sx={(theme) => ({
                ...(isAdded && {
                  borderStyle: "dashed",
                  bgcolor: theme.palette.action.selected,
                }),
                ...(isRemoved && {
                  // The Chip root is a flex container — text-decoration
                  // does not propagate to flex items, so the strike
                  // targets the label span directly.
                  opacity: 0.55,
                  "& .MuiChip-label": { textDecoration: "line-through" },
                }),
              })}
            />
          );
        })}
        <Autocomplete
          freeSolo
          size="small"
          disableClearable
          options={suggestions.filter((s) => !has(s))}
          inputValue={input}
          onInputChange={(_, value) => setInput(value)}
          onChange={(_, value) => {
            if (typeof value === "string" && value.trim() !== "" && !has(value.trim())) {
              setInput("");
              onIntent(group, value.trim(), true);
            }
          }}
          sx={{ minWidth: 170, flexGrow: 1, maxWidth: 260 }}
          renderInput={(params) => (
            <TextField
              {...params}
              inputRef={inputRef}
              placeholder="Pick or type…"
              variant="standard"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitInput();
                }
              }}
            />
          )}
        />
      </Box>
    </Box>
  );
}

export default function PromptLabels({
  baseline,
  effective,
  sessionModified,
  suggestions,
  onCommit,
  focusSignal = 0,
}: PromptLabelsProps) {
  // The change awaiting its save-scope answer (the dialog's subject) —
  // null = no dialog.
  const [pending, setPending] = useState<{
    group: LabelGroup;
    label: string;
    add: boolean;
  } | null>(null);
  // The bound group's add input — the "+" button's landing focus.
  const sectionRef = useRef<HTMLDivElement | null>(null);
  const boundInputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (focusSignal <= 0) return;
    // The main panel is still swapping and the prompts panel may be mid
    // width-transition — wait for the layout to settle, then center the
    // section and focus the bound input (whose auto-scroll on focus can
    // nudge the page — one more centering pass after it).
    const t = setTimeout(() => {
      sectionRef.current?.scrollIntoView({ block: "center" });
      boundInputRef.current?.focus();
      requestAnimationFrame(() =>
        sectionRef.current?.scrollIntoView({ block: "center" }),
      );
    }, 420);
    return () => clearTimeout(t);
  }, [focusSignal]);

  const handleIntent = (group: LabelGroup, label: string, add: boolean) => {
    // A no-op intent (adding an existing / removing an absent label)
    // never opens the dialog.
    if (add === effective[group].includes(label)) return;
    setPending({ group, label, add });
  };

  const close = () => setPending(null);

  return (
    <Box ref={sectionRef} sx={{ mt: 1.5, display: "flex", flexDirection: "column", gap: 1.25 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Labels
        </Typography>
        {sessionModified && (
          <Chip
            label="this run"
            size="small"
            variant="outlined"
            color="warning"
            sx={{ height: 20 }}
          />
        )}
        <Typography variant="caption" color="text.secondary">
          Suggestions are the registry's scope vocabulary — pick one or type
          a custom phrase.
        </Typography>
      </Box>
      {(Object.keys(GROUP_CAPTIONS) as LabelGroup[]).map((group) => (
        <LabelGroupRow
          key={group}
          group={group}
          baseline={baseline[group]}
          effective={effective[group]}
          suggestions={suggestions[group]}
          onIntent={handleIntent}
          inputRef={group === "bound" ? boundInputRef : undefined}
        />
      ))}
      <Dialog open={pending !== null} onClose={close} maxWidth="xs" fullWidth>
        <DialogTitle>Save label change</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            {pending?.add ? "Add" : "Remove"} “{pending?.label}”{" "}
            {pending?.add ? "to" : "from"} the{" "}
            {pending?.group === "bound" ? "bound (force-load)" : "optional (search)"}{" "}
            labels — save it permanently to the prompt registry, or keep it
            just for this run?
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
            Run-only changes are forgotten when you leave this page, and ride
            only the next Generate request.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={close}>Cancel</Button>
          <Button
            onClick={() => {
              if (pending) onCommit(pending.group, pending.label, pending.add, false);
              close();
            }}
          >
            Just this run
          </Button>
          <Button
            variant="contained"
            onClick={() => {
              if (pending) onCommit(pending.group, pending.label, pending.add, true);
              close();
            }}
          >
            Save permanently
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
