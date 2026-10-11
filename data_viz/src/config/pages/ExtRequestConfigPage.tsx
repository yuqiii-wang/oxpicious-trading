/**
 * External request config tab — read + write the
 * external_request_config/ static tree (database/sql/monitoring/static/
 * external_request_config/, one JSON file per service holding its
 * per-request-type pacing / anti-bot rows: sleep cooldown, jitter, poll
 * cadence, retries/backoff, timeouts, toggles). The default.json
 * fallback (repo-wide 60s anti-bot cooldown) is pinned in its own table
 * and editable like any service.
 *
 * Clicking a row opens a DIALOG that edits the row's WHOLE SERVICE file
 * (the row plus any sibling request types — add / delete rows there);
 * Save PUTs the service's complete row list, so an emptied list deletes
 * the service's file and the fallback applies again. Each request type
 * folds to one summary row (accordion — opening one folds the others;
 * the first row starts expanded) revealing narrow fields in wrap rows,
 * each wired to a shared expected-text rule (fieldRules) that validates
 * while typing and blocks Save.
 *
 * Data: GET/PUT /api/config/ext-request (plain fetch — never the shared
 * client cache; these pages mutate the underlying files).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Paper,
  Snackbar,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Add from "@mui/icons-material/Add";
import Delete from "@mui/icons-material/DeleteOutline";
import ErrorOutline from "@mui/icons-material/ErrorOutline";
import ExpandMore from "@mui/icons-material/ExpandMore";
import Refresh from "@mui/icons-material/Refresh";
import {
  fetchExtRequestConfig,
  saveExtRequestConfig,
  type ExtRequestConfigEntry,
} from "@/lib/api-client/config";
import ConfigServiceTable from "@/config/components/ConfigServiceTable";
import RuleTextField from "@/config/components/RuleTextField";
import {
  DOTTED_NAME,
  FREE_TEXT_OR_NULL,
  INT_GTE0_OR_NULL,
  JITTER_OR_NULL,
  NUM_GTE0_OR_NULL,
  REL_PATH_LIST,
  REQUIRED_TEXT,
  firstRuleError,
  numOrNull,
} from "@/config/components/fieldRules";
import { fmtDateTimeShanghai } from "@/shared/datetime";

/** Editable row form — numeric fields as strings while typing; "" = null. */
interface RowForm {
  requestType: string;
  resourceType: string;
  sleepSec: string;
  sleepJitter: string;
  pollIntervalSec: string;
  maxRetries: string;
  retryBackoffSec: string;
  timeoutConnectSec: string;
  timeoutReadSec: string;
  rotateBrowserProfile: boolean;
  addRandomParam: boolean;
  enableHostTracking: boolean;
  hostBlockCooldownSec: string;
  notes: string;
  sourceRef: string;
}

interface RowFormState {
  rows: RowForm[];
}

function toRowForm(r: ExtRequestConfigEntry): RowForm {
  const s = (v: number | null): string => (v == null ? "" : String(v));
  return {
    requestType: r.requestType,
    resourceType: r.resourceType,
    sleepSec: s(r.sleepSec),
    sleepJitter: s(r.sleepJitter),
    pollIntervalSec: s(r.pollIntervalSec),
    maxRetries: s(r.maxRetries),
    retryBackoffSec: s(r.retryBackoffSec),
    timeoutConnectSec: s(r.timeoutConnectSec),
    timeoutReadSec: s(r.timeoutReadSec),
    rotateBrowserProfile: r.rotateBrowserProfile,
    addRandomParam: r.addRandomParam,
    enableHostTracking: r.enableHostTracking,
    hostBlockCooldownSec: s(r.hostBlockCooldownSec),
    notes: r.notes ?? "",
    sourceRef: r.sourceRef,
  };
}

function blankRowForm(): RowForm {
  return {
    requestType: "default",
    resourceType: "http",
    sleepSec: "60",
    sleepJitter: "0.5",
    pollIntervalSec: "",
    maxRetries: "",
    retryBackoffSec: "",
    timeoutConnectSec: "15",
    timeoutReadSec: "60",
    rotateBrowserProfile: true,
    addRandomParam: true,
    enableHostTracking: true,
    hostBlockCooldownSec: "",
    notes: "",
    sourceRef: "",
  };
}

/** One row's whole rule scan — Save blocks on the first violation, while
 *  RuleTextField shows the same rules per field as the user types. */
function rowRuleError(f: RowForm): string | null {
  return firstRuleError([
    ["Request type", REQUIRED_TEXT, f.requestType],
    ["Sleep (s)", NUM_GTE0_OR_NULL, f.sleepSec],
    ["Jitter (0–1)", JITTER_OR_NULL, f.sleepJitter],
    ["Poll (s)", NUM_GTE0_OR_NULL, f.pollIntervalSec],
    ["Max retries", INT_GTE0_OR_NULL, f.maxRetries],
    ["Backoff (s)", NUM_GTE0_OR_NULL, f.retryBackoffSec],
    ["Connect timeout (s)", NUM_GTE0_OR_NULL, f.timeoutConnectSec],
    ["Read timeout (s)", NUM_GTE0_OR_NULL, f.timeoutReadSec],
    ["Host block cooldown (s)", NUM_GTE0_OR_NULL, f.hostBlockCooldownSec],
    ["Source ref", REL_PATH_LIST, f.sourceRef],
  ]);
}

function num(v: number | null): string {
  return v == null ? "—" : String(v);
}

/** Directory filter lists the tree's first two layers only (downloads,
 * downloads.etf, …) — deeper dirs are reached via the layer-2 subtree —
 * and presets the filter to the analysis subtree (no-op until an
 * analysis/ service file exists, via the staleness fallback). */
const DIR_FILTER_MAX_DEPTH = 2;
const DIR_FILTER_DEFAULT_DIR = "analysis";

/** Compact pacing summary: "20s ±30%" / poll cadence when set. */
function pacingCell(r: ExtRequestConfigEntry): string {
  const sleep = r.sleepSec != null ? `${r.sleepSec}s` : null;
  const jitter = r.sleepJitter != null ? `±${Math.round(r.sleepJitter * 100)}%` : null;
  const parts = [sleep != null ? [sleep, jitter].filter(Boolean).join(" ") : null];
  if (r.pollIntervalSec != null) parts.push(`poll ${r.pollIntervalSec}s`);
  return parts.filter(Boolean).join(" · ") || "—";
}

function togglesCell(r: ExtRequestConfigEntry): string {
  const on: string[] = [];
  if (r.rotateBrowserProfile) on.push("browser");
  if (r.addRandomParam) on.push("randparam");
  if (r.enableHostTracking) on.push("hosttrack");
  return on.length > 0 ? on.join(" · ") : "none";
}

/** Collapsed accordion-row summary from the typed form strings — pacing,
 *  poll cadence, retries ("" numeric = null, so skipped). */
function rowSummary(f: RowForm): string {
  const parts: string[] = [];
  if (f.sleepSec !== "") {
    const jitter =
      f.sleepJitter !== "" ? ` ±${Math.round(Number(f.sleepJitter) * 100)}%` : "";
    parts.push(`${f.sleepSec}s${jitter}`);
  }
  if (f.pollIntervalSec !== "") parts.push(`poll ${f.pollIntervalSec}s`);
  if (f.maxRetries !== "") parts.push(`${f.maxRetries} retries`);
  return parts.join(" · ");
}

export default function ExtRequestConfigPage() {
  const [rows, setRows] = useState<ExtRequestConfigEntry[] | null>(null);
  const [defaultRows, setDefaultRows] = useState<ExtRequestConfigEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Dialog state: `service` names the file being edited; `form` holds the
  // service's whole editable row list; `serviceLocked` disables renaming
  // for existing files.
  const [service, setService] = useState<string | null>(null);
  const [serviceLocked, setServiceLocked] = useState(false);
  const [form, setForm] = useState<RowFormState | null>(null);
  // Which request-type accordion is expanded — null = all folded; opening
  // one row folds the others (a failed Save re-opens the offending row).
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await fetchExtRequestConfig();
      setRows(resp.rows);
      setDefaultRows(resp.defaultRows);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const byService = useMemo(() => {
    const map = new Map<string, ExtRequestConfigEntry[]>();
    for (const r of rows ?? []) {
      const list = map.get(r.service);
      if (list) list.push(r);
      else map.set(r.service, [r]);
    }
    return map;
  }, [rows]);

  const openService = useCallback(
    (svc: string, svcRows: ExtRequestConfigEntry[]) => {
      setService(svc);
      setServiceLocked(true);
      setForm({ rows: svcRows.map(toRowForm) });
      // First row starts expanded, the rest folded to summary rows.
      setOpenIdx(0);
      setFormError(null);
    },
    [],
  );

  const openNew = useCallback(() => {
    setService("");
    setServiceLocked(false);
    setForm({ rows: [blankRowForm()] });
    setOpenIdx(0);
    setFormError(null);
  }, []);

  // Close the editor: `form` drives the Dialog's `open`, so a Cancel that
  // only nulled `service` left it mounted — retitled "Edit null".
  const closeEditor = useCallback(() => {
    setService(null);
    setForm(null);
    setOpenIdx(null);
    setFormError(null);
  }, []);

  const knownServices = useMemo(() => new Set(byService.keys()), [byService]);

  const save = useCallback(async () => {
    if (service == null || form == null) return;
    const svc = service.trim();
    const svcErr = DOTTED_NAME.error(svc);
    if (svcErr != null) {
      setFormError(`Service ${svcErr}`);
      return;
    }
    if (!serviceLocked && knownServices.has(svc)) {
      setFormError(`A config file for ${svc} already exists — edit it instead`);
      return;
    }
    for (let i = 0; i < form.rows.length; i++) {
      const f = form.rows[i];
      if (f.resourceType !== "http" && f.resourceType !== "llm") {
        setOpenIdx(i);
        setFormError(`Request type #${i + 1}: resource type must be http or llm`);
        return;
      }
      const err = rowRuleError(f);
      if (err != null) {
        setOpenIdx(i);
        setFormError(`Request type “${f.requestType}”: ${err}`);
        return;
      }
    }
    try {
      const payload = form.rows.map((f) => ({
        requestType: f.requestType.trim(),
        resourceType: f.resourceType,
        sleepSec: numOrNull(f.sleepSec),
        sleepJitter: numOrNull(f.sleepJitter),
        pollIntervalSec: numOrNull(f.pollIntervalSec),
        maxRetries: numOrNull(f.maxRetries),
        retryBackoffSec: numOrNull(f.retryBackoffSec),
        timeoutConnectSec: numOrNull(f.timeoutConnectSec),
        timeoutReadSec: numOrNull(f.timeoutReadSec),
        rotateBrowserProfile: f.rotateBrowserProfile,
        addRandomParam: f.addRandomParam,
        enableHostTracking: f.enableHostTracking,
        hostBlockCooldownSec: numOrNull(f.hostBlockCooldownSec),
        notes: f.notes === "" ? null : f.notes,
        sourceRef: f.sourceRef.trim(),
      }));
      setSaving(true);
      setFormError(null);
      await saveExtRequestConfig(svc, payload);
      setToast(
        payload.length === 0
          ? `Deleted ${svc} — the default fallback now applies`
          : `Saved ${svc} (${payload.length} request type${payload.length === 1 ? "" : "s"})`,
      );
      setService(null);
      setForm(null);
      setOpenIdx(null);
      await load();
    } catch (e) {
      setFormError(String(e).replace(/^Error: /, ""));
    } finally {
      setSaving(false);
    }
  }, [service, form, serviceLocked, knownServices, load]);

  const renderRow = (r: ExtRequestConfigEntry, onOpen: () => void) => (
    <TableRow key={`${r.service}/${r.requestType}`} hover sx={{ cursor: "pointer" }} onClick={onOpen}>
      <TableCell sx={{ fontFamily: "monospace" }}>
        {r.service}
      </TableCell>
      <TableCell sx={{ fontFamily: "monospace" }}>{r.requestType}</TableCell>
      <TableCell>
        <Chip size="small" label={r.resourceType} variant="outlined" />
      </TableCell>
      <TableCell>{pacingCell(r)}</TableCell>
      <TableCell>
        {r.maxRetries == null ? "—" : `${r.maxRetries}${r.retryBackoffSec != null ? ` / ${r.retryBackoffSec}s` : ""}`}
      </TableCell>
      <TableCell>
        {r.timeoutConnectSec == null && r.timeoutReadSec == null
          ? "—"
          : `${num(r.timeoutConnectSec)} / ${num(r.timeoutReadSec)}`}
      </TableCell>
      <TableCell>{togglesCell(r)}</TableCell>
      <TableCell sx={{ maxWidth: 280 }}>
        <Tooltip title={r.notes ?? ""}>
          <Typography variant="body2" noWrap sx={{ maxWidth: 280 }}>
            {r.notes ?? "—"}
          </Typography>
        </Tooltip>
      </TableCell>
      <TableCell>{fmtDateTimeShanghai(r.updatedAt)}</TableCell>
    </TableRow>
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">External Request Config</Typography>
        <Typography variant="caption" color="text.secondary">
          {rows != null
            ? `${byService.size} services · pacing / anti-bot per request type · source: database/sql/monitoring/static/external_request_config/`
            : "loading…"}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title="Reload the tree from disk">
          <span>
            <IconButton size="small" aria-label="reload" onClick={load}>
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Button size="small" variant="outlined" startIcon={<Add />} onClick={openNew}>
          New service
        </Button>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      {defaultRows.length > 0 && (
        <Paper sx={{ overflowX: "auto" }}>
          <Box sx={{ px: 2, pt: 1.5, display: "flex", alignItems: "center", gap: 1 }}>
            <Chip size="small" color="warning" label="fallback" />
            <Typography variant="subtitle2">default.json</Typography>
            <Typography variant="caption" color="text.secondary">
              applied to every service without its own file (repo-wide anti-bot cooldown)
            </Typography>
          </Box>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Service</TableCell>
                <TableCell>Request type</TableCell>
                <TableCell>Resource</TableCell>
                <TableCell>Pacing</TableCell>
                <TableCell>Retries</TableCell>
                <TableCell>Conn / read (s)</TableCell>
                <TableCell>Toggles</TableCell>
                <TableCell>Notes</TableCell>
                <TableCell>Updated</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {defaultRows.map((r) =>
                renderRow(r, () => openService("default", defaultRows)),
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <ConfigServiceTable
        name="Services"
        rows={rows}
        searchText={(r) => `${r.service} ${r.requestType}`}
        maxDepth={DIR_FILTER_MAX_DEPTH}
        defaultDir={DIR_FILTER_DEFAULT_DIR}
        searchPlaceholder="Search service / request type…"
      >
        {(visible, query) => (
          <>
            <TableHead>
              <TableRow>
                <TableCell>Service</TableCell>
                <TableCell>Request type</TableCell>
                <TableCell>Resource</TableCell>
                <TableCell>Pacing</TableCell>
                <TableCell>Retries</TableCell>
                <TableCell>Conn / read (s)</TableCell>
                <TableCell>Toggles</TableCell>
                <TableCell>Notes</TableCell>
                <TableCell>Updated</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map((r) =>
                renderRow(r, () =>
                  openService(r.service, byService.get(r.service) ?? [r]),
                ),
              )}
              {rows != null && visible.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} align="center">
                    <Typography variant="caption" color="text.secondary" sx={{ my: 2, display: "inline-block" }}>
                      No request config matches “{query}”
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </>
        )}
      </ConfigServiceTable>

      <Dialog
        open={form != null}
        onClose={closeEditor}
        fullWidth
        maxWidth={false}
        sx={{ "& .MuiDialog-paper": { maxWidth: 760 } }}
      >
        <DialogTitle>
          {serviceLocked ? `Edit ${service}` : "New service config"}
        </DialogTitle>
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}>
          {formError && <Alert severity="error">{formError}</Alert>}
          <RuleTextField
            rule={DOTTED_NAME}
            label="Service (dotted name)"
            value={service ?? ""}
            onChange={(v) => setService(v)}
            disabled={serviceLocked}
            hint="dotted name: letters/digits/_/- segments, e.g. downloads.etf.huatai_pb.trend · the name IS the tree path: downloads/etf/huatai_pb/trend.json"
            sx={{ fontFamily: "monospace", maxWidth: 480 }}
          />
          {form?.rows.map((f, i) => {
            const badResource = f.resourceType !== "http" && f.resourceType !== "llm";
            const invalid = badResource || rowRuleError(f) != null;
            return (
              <Accordion
                key={i}
                expanded={openIdx === i}
                onChange={() => setOpenIdx((cur) => (cur === i ? null : i))}
                variant="outlined"
                disableGutters
              >
                <AccordionSummary expandIcon={<ExpandMore fontSize="small" />}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexGrow: 1, mr: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      #{i + 1}
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{ fontFamily: "monospace", color: invalid ? "error.main" : undefined }}
                    >
                      {f.requestType === "" ? "(unnamed)" : f.requestType}
                    </Typography>
                    <Chip
                      size="small"
                      label={f.resourceType}
                      variant="outlined"
                      color={badResource ? "error" : "default"}
                    />
                    <Typography variant="caption" color="text.secondary" noWrap>
                      {rowSummary(f)}
                    </Typography>
                    {invalid && <ErrorOutline fontSize="small" color="error" />}
                    <Box sx={{ flexGrow: 1 }} />
                    {form.rows.length > 1 && (
                      <IconButton
                        size="small"
                        aria-label="delete request type"
                        onClick={(e) => {
                          e.stopPropagation();
                          setForm((s) =>
                            s == null ? s : { ...s, rows: s.rows.filter((_, j) => j !== i) },
                          );
                          setOpenIdx((cur) =>
                            cur == null ? cur : cur === i ? null : cur > i ? cur - 1 : cur,
                          );
                        }}
                      >
                        <Delete fontSize="small" />
                      </IconButton>
                    )}
                  </Box>
                </AccordionSummary>
                <AccordionDetails
                  sx={{ display: "flex", flexDirection: "column", gap: 1.5, pt: 0 }}
                >
                  <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "flex-start" }}>
                    <RuleTextField
                      rule={REQUIRED_TEXT}
                      label="Request type"
                      value={f.requestType}
                      onChange={(v) => setForm((s) => (s == null ? s : patchRow(s, i, { requestType: v })))}
                      sx={{ width: 220, fontFamily: "monospace" }}
                    />
                    <TextField
                      select
                      label="Resource"
                      value={f.resourceType}
                      onChange={(e) =>
                        setForm((s) =>
                          s == null ? s : patchRow(s, i, { resourceType: e.target.value }),
                        )
                      }
                      sx={{ width: 140 }}
                    >
                      <MenuItem value="http">http</MenuItem>
                      <MenuItem value="llm">llm</MenuItem>
                    </TextField>
                    <RuleTextField
                      rule={NUM_GTE0_OR_NULL}
                      label="Sleep (s)"
                      value={f.sleepSec}
                      onChange={(v) => setForm((s) => (s == null ? s : patchRow(s, i, { sleepSec: v })))}
                      inputProps={{ inputMode: "decimal" }}
                      sx={{ width: 150 }}
                    />
                    <RuleTextField
                      rule={JITTER_OR_NULL}
                      label="Jitter (0–1)"
                      value={f.sleepJitter}
                      onChange={(v) => setForm((s) => (s == null ? s : patchRow(s, i, { sleepJitter: v })))}
                      hint="number 0–1 · blank = null"
                      inputProps={{ inputMode: "decimal" }}
                      sx={{ width: 150 }}
                    />
                    <RuleTextField
                      rule={NUM_GTE0_OR_NULL}
                      label="Poll (s)"
                      value={f.pollIntervalSec}
                      onChange={(v) =>
                        setForm((s) => (s == null ? s : patchRow(s, i, { pollIntervalSec: v })))
                      }
                      inputProps={{ inputMode: "decimal" }}
                      sx={{ width: 150 }}
                    />
                    <RuleTextField
                      rule={INT_GTE0_OR_NULL}
                      label="Max retries"
                      value={f.maxRetries}
                      onChange={(v) => setForm((s) => (s == null ? s : patchRow(s, i, { maxRetries: v })))}
                      inputProps={{ inputMode: "numeric" }}
                      sx={{ width: 150 }}
                    />
                    <RuleTextField
                      rule={NUM_GTE0_OR_NULL}
                      label="Backoff (s)"
                      value={f.retryBackoffSec}
                      onChange={(v) =>
                        setForm((s) => (s == null ? s : patchRow(s, i, { retryBackoffSec: v })))
                      }
                      inputProps={{ inputMode: "decimal" }}
                      sx={{ width: 150 }}
                    />
                    <RuleTextField
                      rule={NUM_GTE0_OR_NULL}
                      label="Connect timeout (s)"
                      value={f.timeoutConnectSec}
                      onChange={(v) =>
                        setForm((s) => (s == null ? s : patchRow(s, i, { timeoutConnectSec: v })))
                      }
                      inputProps={{ inputMode: "decimal" }}
                      sx={{ width: 160 }}
                    />
                    <RuleTextField
                      rule={NUM_GTE0_OR_NULL}
                      label="Read timeout (s)"
                      value={f.timeoutReadSec}
                      onChange={(v) =>
                        setForm((s) => (s == null ? s : patchRow(s, i, { timeoutReadSec: v })))
                      }
                      inputProps={{ inputMode: "decimal" }}
                      sx={{ width: 160 }}
                    />
                    <RuleTextField
                      rule={NUM_GTE0_OR_NULL}
                      label="Host block cooldown (s)"
                      value={f.hostBlockCooldownSec}
                      onChange={(v) =>
                        setForm((s) => (s == null ? s : patchRow(s, i, { hostBlockCooldownSec: v })))
                      }
                      inputProps={{ inputMode: "decimal" }}
                      sx={{ width: 190 }}
                    />
                  </Box>
                  <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
                    {(
                      [
                        ["rotateBrowserProfile", "rotate browser profile"],
                        ["addRandomParam", "add random param"],
                        ["enableHostTracking", "host tracking"],
                      ] as const
                    ).map(([key, label]) => (
                      <FormControlLabel
                        key={key}
                        control={
                          <Switch
                            size="small"
                            checked={f[key]}
                            onChange={(e) =>
                              setForm((s) =>
                                s == null ? s : patchRow(s, i, { [key]: e.target.checked }),
                              )
                            }
                          />
                        }
                        label={label}
                      />
                    ))}
                  </Box>
                  <RuleTextField
                    rule={FREE_TEXT_OR_NULL}
                    label="Notes"
                    multiline
                    minRows={1}
                    value={f.notes}
                    onChange={(v) => setForm((s) => (s == null ? s : patchRow(s, i, { notes: v })))}
                    sx={{ width: 520, maxWidth: "100%" }}
                  />
                  <RuleTextField
                    rule={REL_PATH_LIST}
                    label="Source ref"
                    value={f.sourceRef}
                    onChange={(v) => setForm((s) => (s == null ? s : patchRow(s, i, { sourceRef: v })))}
                    sx={{ width: 520, maxWidth: "100%" }}
                  />
                </AccordionDetails>
              </Accordion>
            );
          })}
          <Box sx={{ display: "flex", gap: 1 }}>
            <Button
              size="small"
              startIcon={<Add />}
              onClick={() => {
                setForm((s) => (s == null ? s : { ...s, rows: [...s.rows, blankRowForm()] }));
                // Expand the fresh row (folding any other open one).
                setOpenIdx(form?.rows.length ?? null);
              }}
            >
              Add request type
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Typography variant="caption" color="text.secondary">
              blank number = null · saving with zero rows deletes the service file
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={closeEditor}>
            Cancel
          </Button>
          <Button variant="contained" disabled={saving} onClick={save}>
            Save
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={toast != null}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        message={toast}
      />
    </Box>
  );
}

/** Immutably patch one row form of the dialog's list. */
function patchRow(s: RowFormState, i: number, patch: Partial<RowForm>): RowFormState {
  return {
    ...s,
    rows: s.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)),
  };
}
