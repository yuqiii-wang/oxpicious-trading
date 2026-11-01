/**
 * StatusChip — the deep-agent state/runner status chip shared by the
 * Thread Graph view's context panel and summaries: the text.deep_agent_states
 * lifecycle vocabulary (pending | in_progress | completed | failed |
 * skipped) plus the derived runner "neutral". Compact mode renders the
 * dot only (the step lists' status column).
 */
import { Box, Chip, Tooltip } from "@mui/material";
import type { ThreadNodeStatus } from "./graphModel";

/** Chart-palette CSS-var tokens — same mapping as the graph nodes
 *  (threadGraphOption.statusColor), so panel and canvas never diverge. */
function statusHex(status: ThreadNodeStatus): string {
  switch (status) {
    case "completed":
      return "var(--chart-up, #27ae60)";
    case "failed":
      return "var(--chart-down, #c0392b)";
    case "in_progress":
      return "var(--chart-hi, #2980b9)";
    case "skipped":
      return "var(--chart-subtitle, #7A8190)";
    case "pending":
      return "var(--chart-neutral-fill, #95a5a6)";
    default:
      return "var(--chart-axis-line-light, #9AA4B8)";
  }
}

const LABEL: Record<ThreadNodeStatus, string> = {
  pending: "pending",
  in_progress: "in progress",
  completed: "completed",
  failed: "failed",
  skipped: "skipped",
  neutral: "graph node",
};

export default function StatusChip({
  status,
  compact = false,
}: {
  status: ThreadNodeStatus;
  compact?: boolean;
}) {
  const hex = statusHex(status);
  if (compact) {
    return (
      <Tooltip title={LABEL[status]}>
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            bgcolor: hex,
            display: "inline-block",
            flexShrink: 0,
          }}
        />
      </Tooltip>
    );
  }
  return (
    <Chip
      size="small"
      sx={{
        height: 20,
        fontSize: 11,
        color: hex,
        borderColor: hex,
        bgcolor: "transparent",
      }}
      variant="outlined"
      label={LABEL[status]}
    />
  );
}
