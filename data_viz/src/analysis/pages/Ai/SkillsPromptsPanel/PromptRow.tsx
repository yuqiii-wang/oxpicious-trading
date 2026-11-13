/** One prompt row of the Prompts panel. */
import { Box, Checkbox, Chip, IconButton, Radio, Typography } from "@mui/material";
import { Add } from "@mui/icons-material";
import type { AiPrompt } from "@/lib/api-client/aiSummary";
import { ROW_LABEL_TAGS } from "./panelConstants";

/** One prompt row — the checkbox (selectable rows only; tool rows have
 *  none) + the click target that opens the prompt in the main panel
 *  (again = closes). *exclusive* renders a RADIO instead of the checkbox
 *  (the plan-goal sub type: mutually exclusive — the panel clears the
 *  siblings on select, and the sub-group's Auto entry is the deselected
 *  state). *disabled* (goal rows out of scope: codes-family vs
 *  market-family against the current pick) grays the radio only — the
 *  row itself stays click-to-open for view/edit — and shows
 *  *disabledNote* under the caption. Beneath the caption the row shows
 *  its EXISTING labels as tags (bound = tinted, optional = plain; "+n"
 *  past four) with a "+" add button that opens the prompt focused on
 *  the labels input. */
export function PromptRow({
  prompt,
  showVersionSuffix,
  selectable = true,
  exclusive = false,
  disabled = false,
  disabledNote,
  selected,
  opened,
  onToggle,
  onOpen,
  onAddLabel,
}: {
  prompt: AiPrompt;
  showVersionSuffix: boolean;
  selectable?: boolean;
  exclusive?: boolean;
  disabled?: boolean;
  disabledNote?: string;
  selected: boolean;
  opened: boolean;
  onToggle?: (checked: boolean) => void;
  onOpen: () => void;
  onAddLabel?: () => void;
}) {
  // Bound tags first (the force-load vocabulary), optional after; the
  // rest collapse into "+n".
  const tags = [
    ...prompt.bound_labels.map((label) => ({ label, bound: true })),
    ...prompt.optional_labels.map((label) => ({ label, bound: false })),
  ];
  const visibleTags = tags.slice(0, ROW_LABEL_TAGS);
  const overflow = tags.length - visibleTags.length;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 0.5,
        borderRadius: 1,
        px: 0.5,
        bgcolor: opened ? "action.selected" : "transparent",
        "&:hover": { bgcolor: opened ? "action.selected" : "action.hover" },
      }}
    >
      {selectable && (
        exclusive ? (
          <Radio
            size="small"
            name="plan-goal-scope"
            sx={{ p: 0.5, mt: 0.5 }}
            checked={selected}
            disabled={disabled}
            onChange={() => onToggle?.(true)}
            inputProps={{ "aria-label": `Select ${prompt.title} as the plan goal` }}
          />
        ) : (
          <Checkbox
            size="small"
            sx={{ p: 0.5, mt: 0.5 }}
            checked={selected}
            onChange={(e) => onToggle?.(e.target.checked)}
          />
        )
      )}
      <Box onClick={onOpen} sx={{ minWidth: 0, py: 0.5, cursor: "pointer", flexGrow: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {prompt.title}
          {showVersionSuffix && (
            <Box component="span" sx={{ fontWeight: 400, color: "text.secondary" }}>
              {" "}· v{prompt.version}
            </Box>
          )}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {prompt.preview || prompt.note || prompt.instruction}
        </Typography>
        {disabled && disabledNote !== undefined && (
          <Typography variant="caption" sx={{ display: "block", fontStyle: "italic" }}>
            {disabledNote}
          </Typography>
        )}
        {tags.length > 0 && (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.25, alignItems: "center", mt: 0.25 }}>
            {visibleTags.map(({ label, bound }) => (
              <Chip
                key={label}
                label={label}
                size="small"
                variant="outlined"
                color={bound ? "primary" : "default"}
                sx={{
                  height: 17,
                  fontSize: 10,
                  "& .MuiChip-label": { px: 0.6 },
                }}
              />
            ))}
            {overflow > 0 && (
              <Typography variant="caption" color="text.secondary">
                +{overflow}
              </Typography>
            )}
          </Box>
        )}
      </Box>
      {onAddLabel && (
        <IconButton
          size="small"
          aria-label={`Add label to ${prompt.title}`}
          title="Add label"
          onClick={(e) => {
            // Not an open — just jump to the labels input.
            e.stopPropagation();
            onAddLabel();
          }}
          sx={{ p: 0.25, mt: 0.5 }}
        >
          <Add fontSize="small" />
        </IconButton>
      )}
    </Box>
  );
}
