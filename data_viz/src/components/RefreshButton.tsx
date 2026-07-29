/**
 * RefreshButton — small icon button that bypasses the frontend LRU cache and
 * re-fetches the data behind a page or plot.
 *
 * Two visual sizes:
 *   • default (size="small") — icon + optional label, used in page headers
 *     (next to the title) and ChartCard `action` slots.
 *   • size="tiny"            — icon only, used in tight ChartCard headers
 *     that already have other actions (e.g. EtfMarginPanel's return badges).
 *
 * Behaviour:
 *   1. Caller passes an `onClick` that invalidates the relevant cache keys
 *      (via invalidateCacheForUrl / invalidateCacheForPrefix in api-client)
 *      and bumps a refresh-key state to retrigger the data fetch effect.
 *   2. While the parent's loading state is true, the icon is replaced by a
 *      spinning CircularProgress and the button is disabled.
 *
 * The button does NOT directly trigger a fetch — fetching is the parent's
 * responsibility (via its useEffect deps). This keeps data flow predictable
 * and avoids double-fetches if the parent's effect re-runs for other
 * reasons (filter change, code change, etc.).
 */
import { IconButton, Tooltip, CircularProgress, Box } from "@mui/material";
import { Refresh as RefreshIcon } from "@mui/icons-material";

interface Props {
  /** Click handler — should invalidate cache + bump refresh key. */
  onClick: () => void;
  /** When true, the icon becomes a spinner and the button is disabled. */
  loading?: boolean;
  /** Optional tooltip text. Defaults to "Refresh data (bypass cache)". */
  tooltip?: string;
  /** Optional text label shown next to the icon. Omit for icon-only. */
  label?: string;
  /** Visual size. "tiny" hides the hover background and shrinks the icon. */
  size?: "small" | "tiny";
  /** Disable the button (e.g. when there is no data yet to refresh). */
  disabled?: boolean;
}

export default function RefreshButton({
  onClick,
  loading = false,
  tooltip = "Refresh data (bypass cache)",
  label,
  size = "small",
  disabled = false,
}: Props) {
  const iconFontSize = size === "tiny" ? 16 : 18;
  const spinnerSize = size === "tiny" ? 16 : 18;

  const inner = loading ? (
    <CircularProgress size={spinnerSize} thickness={5} />
  ) : (
    <RefreshIcon sx={{ fontSize: iconFontSize }} />
  );

  if (size === "tiny") {
    // Tight icon-only button — used in ChartCard headers that already
    // host other actions (return badges, period toggles, etc.).
    return (
      <Tooltip title={tooltip} arrow>
        <span>
          <IconButton
            aria-label={tooltip}
            onClick={onClick}
            disabled={disabled || loading}
            size="small"
            sx={{ p: 0.25 }}
          >
            {inner}
          </IconButton>
        </span>
      </Tooltip>
    );
  }

  // Default: icon + optional label, used in page headers and standalone
  // ChartCard action slots.
  return (
    <Tooltip title={tooltip} arrow>
      <span>
        <IconButton
          aria-label={tooltip}
          onClick={onClick}
          disabled={disabled || loading}
          size="small"
          color="primary"
          sx={{
            bgcolor: "action.hover",
            "&:hover": { bgcolor: "action.selected" },
            borderRadius: 1,
            px: label ? 1 : 0.5,
            py: 0.5,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {inner}
            {label && (
              <span style={{ fontSize: "0.75rem", fontWeight: 600 }}>{label}</span>
            )}
          </Box>
        </IconButton>
      </span>
    </Tooltip>
  );
}
