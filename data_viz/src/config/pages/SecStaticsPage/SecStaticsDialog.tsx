/**
 * SecStaticsDialog — the Security Classification page's per-file popup:
 * one database/sec_statics/ JSON shown as a PLAIN ITEM TABLE
 * (JsonTreeTable — rows are the JSON's items, item fields spread into
 * table columns, containers of objects flatten to plain rows; details
 * in JsonTreeTable),
 * with a Raw JSON mode keeping the hand-edit path
 * (verbatim text editor + Format / Reload / Save PUT, identical semantics
 * to the pre-refactor editor: save round-trips the exact text, so a
 * no-change save produces no git diff).
 *
 * Table mode is read-only and available only while the current text parses;
 * raw edits re-validate on every keystroke and the Table toggle re-enables
 * once valid. Generated files carry the same temporary-edits warning chip.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  IconButton,
  Snackbar,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import AutoFixHigh from "@mui/icons-material/AutoFixHigh";
import Close from "@mui/icons-material/Close";
import Code from "@mui/icons-material/Code";
import Refresh from "@mui/icons-material/Refresh";
import Save from "@mui/icons-material/Save";
import TableChart from "@mui/icons-material/TableChart";
import {
  fetchSecStaticsFile,
  saveSecStaticsFile,
  type SecStaticsFile,
} from "@/lib/api-client/config";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import JsonTreeTable from "./JsonTreeTable";

type ParseResult =
  | { kind: "ok"; value: unknown }
  | { kind: "error"; message: string };

function parseJsonText(text: string): ParseResult {
  try {
    return { kind: "ok", value: JSON.parse(text) as unknown };
  } catch (e) {
    return {
      kind: "error",
      message: String(e).replace(/^SyntaxError: /, ""),
    };
  }
}

export interface SecStaticsDialogProps {
  /** Tree-relative path of the open file; null = closed. */
  path: string | null;
  onClose: () => void;
  /** Called after a successful save so the page refreshes its inventory. */
  onSaved: () => void;
}

export default function SecStaticsDialog({ path, onClose, onSaved }: SecStaticsDialogProps) {
  const [fileMeta, setFileMeta] = useState<SecStaticsFile | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [savedText, setSavedText] = useState<string | null>(null);
  const [mode, setMode] = useState<"table" | "raw">("table");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const open = path != null;

  const load = useCallback(async (filePath: string) => {
    setText(null);
    setSavedText(null);
    setFileMeta(null);
    setLoadError(null);
    setJsonError(null);
    setMode("table");
    try {
      const resp = await fetchSecStaticsFile(filePath);
      setText(resp.text);
      setSavedText(resp.text);
      setFileMeta(resp.file);
    } catch (e) {
      setLoadError(String(e).replace(/^Error: /, ""));
    }
  }, []);

  useEffect(() => {
    if (path != null) load(path);
  }, [path, load]);

  const dirty = text != null && savedText != null && text !== savedText;
  const parsed = useMemo<ParseResult | null>(
    () => (text == null ? null : parseJsonText(text)),
    [text],
  );
  // Raw edits re-validate as they type; an invalid doc pins raw mode.
  const tableAvailable = parsed?.kind === "ok";

  const switchMode = (next: "table" | "raw") => {
    if (next === "table" && !tableAvailable) return;
    setMode(next);
  };

  const format = () => {
    if (text == null) return;
    const result = parseJsonText(text);
    if (result.kind === "error") {
      setJsonError(result.message);
      return;
    }
    setText(`${JSON.stringify(result.value, null, 2)}\n`);
    setJsonError(null);
  };

  const save = async () => {
    if (path == null || text == null) return;
    const result = parseJsonText(text);
    if (result.kind === "error") {
      setJsonError(result.message);
      return;
    }
    setSaving(true);
    try {
      const resp = await saveSecStaticsFile(path, text);
      setSavedText(text);
      setFileMeta(resp.file);
      setToast(`Saved ${path}`);
      onSaved();
    } catch (e) {
      setToast(`Save failed — ${String(e).replace(/^Error: /, "")}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="xl"
      PaperProps={{ sx: { height: "82vh" } }}
    >
      {path != null && (
        <>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              flexWrap: "wrap",
              px: 2,
              pt: 2,
            }}
          >
            <Typography variant="subtitle1" sx={{ fontFamily: "monospace" }}>
              {path}
            </Typography>
            {fileMeta?.generated && (
              <Tooltip title="Generated file — builds.classification rewrites it on every run; edits survive only until the next build">
                <Chip size="small" color="warning" label="generated — edits are temporary" />
              </Tooltip>
            )}
            {dirty && <Chip size="small" color="info" label="unsaved" />}
            <Box sx={{ flexGrow: 1 }} />
            <ToggleButtonGroup size="small" exclusive value={mode} onChange={(_, m) => m != null && switchMode(m)}>
              <ToggleButton value="table" disabled={!tableAvailable}>
                <TableChart fontSize="small" sx={{ mr: 0.5 }} /> Table
              </ToggleButton>
              <ToggleButton value="raw">
                <Code fontSize="small" sx={{ mr: 0.5 }} /> Raw JSON
              </ToggleButton>
            </ToggleButtonGroup>
            <IconButton size="small" aria-label="close" onClick={onClose}>
              <Close fontSize="small" />
            </IconButton>
          </Box>
          <Box sx={{ px: 2, pt: 0.5 }}>
            <Typography variant="caption" color="text.secondary">
              {fileMeta
                ? `${fileMeta.sizeBytes.toLocaleString()} B · modified ${fmtDateTimeShanghai(fileMeta.updatedAt)}`
                : ""}
            </Typography>
          </Box>
          <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1, overflow: "hidden" }}>
            {loadError != null && <Alert severity="error">Failed to load {path}: {loadError}</Alert>}
            {text == null && loadError == null && <CircularProgress size={20} sx={{ m: 4, mx: "auto" }} />}
            {text != null && mode === "table" && parsed?.kind === "ok" && (
              <JsonTreeTable value={parsed.value} maxHeight="100%" />
            )}
            {text != null && mode === "raw" && (
              <>
                {jsonError != null && <Alert severity="error">Invalid JSON — {jsonError}</Alert>}
                <TextField
                  fullWidth
                  multiline
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value);
                    const result = parseJsonText(e.target.value);
                    setJsonError(result.kind === "error" ? result.message : null);
                  }}
                  spellCheck={false}
                  sx={{
                    flex: 1,
                    minHeight: 0,
                    "& .MuiInputBase-root": { height: "100%", alignItems: "flex-start" },
                    "& .MuiInputBase-input": {
                      fontFamily: "monospace",
                      fontSize: "0.8rem",
                      lineHeight: 1.5,
                      height: "100% !important",
                      overflow: "auto !important",
                    },
                  }}
                />
              </>
            )}
          </DialogContent>
          <DialogActions sx={{ px: 2, pb: 1.5 }}>
            {mode === "raw" && (
              <Button size="small" variant="outlined" startIcon={<AutoFixHigh />} onClick={format}>
                Format
              </Button>
            )}
            <Button
              size="small"
              color="inherit"
              startIcon={<Refresh />}
              disabled={path == null || saving}
              onClick={() => path != null && load(path)}
            >
              Reload
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Button
              size="small"
              variant="contained"
              startIcon={saving ? undefined : <Save />}
              disabled={saving || !dirty}
              onClick={save}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogActions>
        </>
      )}
      <Snackbar
        open={toast != null}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        message={toast}
      />
    </Dialog>
  );
}
