/**
 * LogViewerDialog — large full-height display for ONE run's log file
 * (data-agnostic: the caller owns the fetch and passes the view in).
 *
 * A near-fullscreen dialog holding a monospace <pre> with the log tail,
 * auto-scrolled to the BOTTOM on load (the end of a log is its newest
 * content), a contains-filter that hides non-matching lines (with the
 * match count), copy-to-clipboard, and a truncation notice (total lines /
 * size) so a capped tail is never mistaken for the whole file. The
 * no-file state lists the candidate component names that were tried.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  Paper,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Close from "@mui/icons-material/Close";
import ContentCopy from "@mui/icons-material/ContentCopy";
import Search from "@mui/icons-material/Search";

/** The log payload the viewer renders — the caller maps its API shape onto
 * this (file is a display path; candidates explain a not-found result). */
export interface LogFileView {
  file: string | null;
  found: boolean;
  content: string;
  truncated: boolean;
  totalLines: number;
  sizeBytes: number;
  /** Candidate component names tried (shown when file is null). */
  candidates?: string[];
}

interface LogViewerDialogProps {
  open: boolean;
  onClose: () => void;
  /** Heading — the service / component the log belongs to. */
  title: string;
  /** One-line run meta under the heading (started → ended · exit). */
  subtitle?: string | null;
  /** The log view (null while loading / before the fetch resolves). */
  log: LogFileView | null;
  loading: boolean;
  error: string | null;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function LogViewerDialog({
  open,
  onClose,
  title,
  subtitle,
  log,
  loading,
  error,
}: LogViewerDialogProps) {
  // Contains-filter over the log's lines — non-matching lines are hidden
  // (the filter narrows a large tail, it does not search the whole file).
  const [filter, setFilter] = useState("");
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  // Fresh log (or a filter change) repositions: bottom for the unfiltered
  // view (newest lines), top when filtering (matches read top-down).
  useEffect(() => {
    const el = preRef.current;
    if (el == null) return;
    el.scrollTop = filter.trim() === "" ? el.scrollHeight : 0;
  }, [log, filter]);

  // Reset the transient UI state whenever the dialog reopens on a new log.
  useEffect(() => {
    if (open) {
      setFilter("");
      setCopied(false);
    }
  }, [open, log]);

  const filterActive = filter.trim() !== "";
  const { display, matchLines } = useMemo(() => {
    if (log == null) return { display: "", matchLines: 0 };
    if (!filterActive) return { display: log.content, matchLines: log.totalLines };
    const needle = filter.trim().toLowerCase();
    const hits = log.content.split("\n").filter((l) => l.toLowerCase().includes(needle));
    return { display: hits.join("\n"), matchLines: hits.length };
  }, [log, filterActive, filter]);

  const copy = async () => {
    if (log == null) return;
    try {
      await navigator.clipboard.writeText(log.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard unavailable (permissions) — nothing to do
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      fullWidth
      sx={{ "& .MuiDialog-paper": { width: "min(1500px, 96vw)", height: "90vh" } }}
    >
      <DialogTitle sx={{ display: "flex", alignItems: "flex-start", gap: 1, pr: 1 }}>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="h6" noWrap sx={{ fontFamily: "monospace" }}>
            {title}
          </Typography>
          {subtitle != null && (
            <Typography variant="caption" color="text.secondary">
              {subtitle}
            </Typography>
          )}
        </Box>
        <IconButton size="small" aria-label="close log viewer" onClick={onClose}>
          <Close fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 1, overflow: "hidden", pt: 0.5 }}
      >
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={24} />
          </Box>
        )}
        {!loading && error != null && <Alert severity="error">{error}</Alert>}
        {!loading && error == null && log != null && !log.found && (
          <Alert severity="info">
            No log file on record for this run&apos;s date
            {log.candidates != null && log.candidates.length > 0 && (
              <> — tried: {log.candidates.map((c) => `${c}.log`).join(", ")}</>
            )}
            . The run may predate the per-service logs/ tree (older runs
            keep their legacy component-named file).
          </Alert>
        )}
        {!loading && error == null && log?.found && (
          <>
            {/* File meta + truncation notice + filter + copy, one row. */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
              <Chip
                size="small"
                label={log.file}
                sx={{ fontFamily: "monospace", maxWidth: 420 }}
              />
              <Typography variant="caption" color="text.secondary">
                {log.totalLines.toLocaleString()} lines · {fmtSize(log.sizeBytes)}
              </Typography>
              {log.truncated && (
                <Tooltip title="The file was capped to its last 2000 lines for display — copy grabs only what is shown">
                  <Chip
                    size="small"
                    color="warning"
                    variant="outlined"
                    label="showing last 2000 lines"
                  />
                </Tooltip>
              )}
              {filterActive && (
                <Typography variant="caption" color="text.secondary">
                  {matchLines.toLocaleString()} matching line
                  {matchLines === 1 ? "" : "s"}
                </Typography>
              )}
              <Box sx={{ flexGrow: 1 }} />
              <TextField
                size="small"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter lines containing…"
                sx={{ width: 260 }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Search fontSize="small" sx={{ color: "text.secondary" }} />
                    </InputAdornment>
                  ),
                }}
              />
              <Tooltip title={copied ? "Copied" : "Copy the unfiltered log content"}>
                <span>
                  <IconButton
                    size="small"
                    aria-label="copy log"
                    color={copied ? "success" : "default"}
                    onClick={copy}
                  >
                    <ContentCopy fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </Box>
            <PaperLog log={display} preRef={preRef} />
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** The outlined, scrollable monospace body. Kept as a separate component
 *  so the ref lands on the <pre> that scrolls. */
function PaperLog({
  log,
  preRef,
}: {
  log: string;
  preRef: React.RefObject<HTMLPreElement | null>;
}) {
  return (
    <Paper
      variant="outlined"
      sx={{ flex: 1, minHeight: 0, overflow: "auto", bgcolor: "background.default" }}
    >
      <pre
        ref={preRef}
        style={{
          margin: 0,
          padding: "8px 12px",
          fontFamily: "monospace",
          fontSize: "0.75rem",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {log === "" ? "(empty log file)" : log}
      </pre>
    </Paper>
  );
}
