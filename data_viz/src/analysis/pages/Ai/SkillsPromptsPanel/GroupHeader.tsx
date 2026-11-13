/** A group (or sub-group) header of the Prompts panel. */
import { Box, Checkbox, IconButton, Typography } from "@mui/material";
import { ExpandLess, ExpandMore } from "@mui/icons-material";
import type { AiPrompt } from "@/lib/api-client/aiSummary";

/** A group's (or sub-group's) header: the select-all checkbox
 *  (selectable groups only — the Tools group and the plan type group
 *  show a count instead: the Tools count is plain rows, the plan count
 *  is picked/total via *showSelectionCount*, since its goal sub type is
 *  a radio set a select-all cannot drive), the click-to-fold title —
 *  type-level titles larger (subtitle2) than the sub-group captions —
 *  the picked/total count and the chevron. */
export function GroupHeader({
  label,
  rows,
  selectedKeys,
  collapsed,
  onToggleCollapsed,
  onToggleAll,
  subGroup,
  selectable = true,
  showSelectionCount = false,
}: {
  label: string;
  rows: AiPrompt[];
  selectedKeys?: Set<string>;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onToggleAll?: (checked: boolean) => void;
  subGroup?: boolean;
  selectable?: boolean;
  showSelectionCount?: boolean;
}) {
  const checkedCount = rows.filter((row) => selectedKeys?.has(row.prompt_key) ?? false).length;
  const allChecked = selectable && checkedCount === rows.length;
  const countText = selectable || showSelectionCount
    ? `${checkedCount}/${rows.length}`
    : `${rows.length}`;
  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      {selectable && (
        <Checkbox
          size="small"
          checked={allChecked}
          indeterminate={checkedCount > 0 && !allChecked}
          onChange={(e) => onToggleAll?.(e.target.checked)}
          inputProps={{ "aria-label": `Select all ${label} prompts` }}
          sx={{ py: 0.5 }}
        />
      )}
      <Typography
        variant={subGroup ? "caption" : "subtitle2"}
        onClick={onToggleCollapsed}
        sx={{ fontWeight: 600, cursor: "pointer", userSelect: "none", flexGrow: 1 }}
      >
        {label}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
        {countText}
      </Typography>
      <IconButton size="small" onClick={onToggleCollapsed} sx={{ p: 0.25 }}>
        {collapsed ? <ExpandMore fontSize="small" /> : <ExpandLess fontSize="small" />}
      </IconButton>
    </Box>
  );
}
