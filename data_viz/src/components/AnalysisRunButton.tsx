/**
 * AnalysisRunButton — per-security "build this security" refresh button.
 *
 * Rendered in the header (ChartCard action slot) of every per-security
 * analysis panel (MA-Spread / Recurring Cycles / PE & Dividend). Clicking it
 * runs the panel's Python analysis main for THIS security only:
 *
 *   python -m analyze.<module> --sec-type <secType> --code <code>
 *
 * and calls `onCompleted` when the run finishes so the parent can
 * invalidate its cache and refetch the panel data.
 *
 * Visual states:
 *   • hasData === false → BOLD highlight (accent border + bold "Build"
 *     label): the security has no analysis rows, so building them is the
 *     primary action for this panel.
 *   • running → spinner (disabled). The running state is also polled on
 *     mount via the process-id-tag registry, so a page refresh while a
 *     remote run continues puts the button straight back into its
 *     spinning state until the process exits.
 *   • error → the tooltip carries the stderr tail of the failed run.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Box, CircularProgress, IconButton, Tooltip } from "@mui/material";
import { Refresh as RefreshIcon } from "@mui/icons-material";
import {
  analysisRunTag,
  fetchAnalysisRunStatus,
  runAnalysisForSecurity,
  type RunnableAnalysisModule,
} from "@/lib/api-client";

interface Props {
  /** Analysis main to run (python -m analyze.<module>). */
  module: RunnableAnalysisModule;
  /** Security type ('etf' | 'index' | 'stock'). */
  secType: string;
  /** Security code — the run is scoped to this security only. */
  code: string;
  /**
   * false when the panel has no analysis data (fetch error or empty
   * result) — bolds the button to highlight the missing-data state.
   */
  hasData: boolean;
  /**
   * Called after a run completes (success OR failure) — the parent
   * invalidates its cache and refetches the panel data.
   */
  onCompleted: () => void;
  /** Visual size — "tiny" hides the label (tight card headers). */
  size?: "small" | "tiny";
}

/** Poll interval (ms) while a remote run (started elsewhere / before a
 *  page refresh) is still in flight. */
const REMOTE_POLL_MS = 3000;

export default function AnalysisRunButton({
  module,
  secType,
  code,
  hasData,
  onCompleted,
  size = "small",
}: Props) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the latest onCompleted without re-triggering the effects below.
  const onCompletedRef = useRef(onCompleted);
  useEffect(() => {
    onCompletedRef.current = onCompleted;
  }, [onCompleted]);

  const tag = analysisRunTag(module, secType, code);
  const cmd = `python -m analyze.${module} --sec-type ${secType} --code ${code}`;
  const missing = !hasData && !running;

  // ---- Mount: restore the spinner if this security's run is already ----
  // ---- in flight (page refresh / second tab), then poll until done. ----
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const status = await fetchAnalysisRunStatus([tag]);
        if (cancelled) return;
        if (status[tag]) {
          setRunning(true);
          timer = setTimeout(poll, REMOTE_POLL_MS);
        } else {
          setRunning(false);
        }
      } catch {
        if (!cancelled) {
          // Status endpoint unreachable — don't spin forever.
          setRunning(false);
        }
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [tag]);

  // ---- Click: run the analysis main for THIS security, then refetch. ----
  const handleClick = useCallback(async () => {
    setRunning(true);
    setError(null);
    const res = await runAnalysisForSecurity(module, secType, code);
    setRunning(false);
    if (!res.success && !res.already_running) {
      setError(res.stderr_tail ?? `exit code unknown`);
    }
    // Refetch regardless — a failed run may still leave partial data,
    // and a successful one must be picked up immediately.
    onCompletedRef.current();
  }, [module, secType, code]);

  const tooltip = running
    ? `Running: ${cmd}…`
    : missing
      ? `No analysis data for ${code} — click to run:${"\n"}${cmd}` +
        (error ? `${"\n"}Last run failed: ${error}` : "")
      : `Rebuild analysis rows for this security:${"\n"}${cmd}` +
        (error ? `${"\n"}Last run failed: ${error}` : "");

  const label = size === "tiny" ? undefined : missing ? "Build" : undefined;

  return (
    <Tooltip title={tooltip} arrow>
      <span>
        <IconButton
          aria-label={tooltip}
          onClick={handleClick}
          disabled={running}
          size="small"
          color={missing ? "warning" : "primary"}
          sx={{
            bgcolor: "action.hover",
            "&:hover": { bgcolor: "action.selected" },
            borderRadius: 1,
            px: 0.75,
            py: 0.5,
            ...(missing
              ? {
                  // Bold highlight: thick accent border + bold label —
                  // the security is missing its analysis data.
                  border: "2px solid",
                  borderColor: "warning.main",
                  bgcolor: "rgba(237, 108, 2, 0.12)",
                  "&:hover": { bgcolor: "rgba(237, 108, 2, 0.22)" },
                }
              : {}),
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {running ? (
              <CircularProgress size={18} thickness={5} />
            ) : (
              <RefreshIcon sx={{ fontSize: 18 }} />
            )}
            {label && (
              <span
                style={{
                  fontSize: "0.75rem",
                  fontWeight: 800,
                  letterSpacing: 0.2,
                }}
              >
                {label}
              </span>
            )}
          </Box>
        </IconButton>
      </span>
    </Tooltip>
  );
}
