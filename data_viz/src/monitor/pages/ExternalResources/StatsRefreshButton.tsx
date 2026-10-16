/**
 * StatsRefreshButton — re-runs the per-day aggregation behind the
 * External Resources page (python -m database.external_resource_stats,
 * via POST /api/monitor/ext-resources/refresh) and calls `onCompleted`
 * when it finishes so the parent refetches the trailing window. The
 * stats table is COMPUTED — the stats endpoint never aggregates on
 * read — so this button is the only way a day shows up before the next
 * scheduled run.
 *
 * Visual states (mirrors AnalysisRunButton):
 *   • running → spinner (disabled); restored on mount by polling the
 *     process-id-tag registry, so a page refresh while a run continues
 *     (started here or in another tab) keeps spinning until it exits.
 *   • error → the tooltip carries the stderr tail of the failed run.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Box, CircularProgress, IconButton, Tooltip } from "@mui/material";
import { Refresh as RefreshIcon } from "@mui/icons-material";
import {
  fetchExtResourceStatsRefreshRunning,
  runExtResourceStatsRefresh,
} from "@/lib/api-client/monitor";

/** Poll interval (ms) while a remote run is still in flight. */
const REMOTE_POLL_MS = 3000;

export default function StatsRefreshButton({
  onCompleted,
}: {
  /** Called after the run settles (success OR failure) — the parent
   *  invalidates its stats cache and refetches. */
  onCompleted: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the latest onCompleted without re-triggering the effect below.
  const onCompletedRef = useRef(onCompleted);
  useEffect(() => {
    onCompletedRef.current = onCompleted;
  }, [onCompleted]);

  // ---- Mount: restore the spinner if a refresh is already in flight ----
  // ---- (page refresh / second tab), then poll until it exits.        ----
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const isRunning = await fetchExtResourceStatsRefreshRunning();
        if (cancelled) return;
        if (isRunning) {
          setRunning(true);
          timer = setTimeout(poll, REMOTE_POLL_MS);
        } else {
          setRunning(false);
        }
      } catch {
        // Status endpoint unreachable — don't spin forever.
        if (!cancelled) setRunning(false);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // ---- Click: run the incremental stats refresh, then refetch. ----
  const handleClick = useCallback(async () => {
    setRunning(true);
    setError(null);
    const res = await runExtResourceStatsRefresh();
    setRunning(false);
    if (!res.success && !res.alreadyRunning) {
      setError(res.stderrTail || `exit code ${res.exitCode}`);
    }
    // Refetch regardless — a failed run may still have rebuilt some
    // dates, and a successful one must be picked up immediately.
    onCompletedRef.current();
  }, []);

  const tooltip = running
    ? "Re-running the per-day stats aggregation…"
    : "Re-run the per-day stats aggregation"
      + `${"\n"}(python -m database.external_resource_stats)`
      + (error ? `${"\n"}Last run failed: ${error}` : "");

  return (
    <Tooltip title={tooltip} arrow>
      <span>
        <IconButton
          aria-label={tooltip}
          onClick={handleClick}
          disabled={running}
          size="small"
          sx={{
            bgcolor: "action.hover",
            "&:hover": { bgcolor: "action.selected" },
            borderRadius: 1,
            px: 0.75,
            py: 0.5,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center" }}>
            {running ? (
              <CircularProgress size={18} thickness={5} />
            ) : (
              <RefreshIcon sx={{ fontSize: 18 }} />
            )}
          </Box>
        </IconButton>
      </span>
    </Tooltip>
  );
}
