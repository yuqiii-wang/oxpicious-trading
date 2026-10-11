/**
 * Service config tab — read + write the service_config/ static tree
 * (database/sql/monitoring/static/service_config/, one JSON file per
 * service: concurrency cap + supposed-run cadence mirrored from
 * scripts/*.sh). The default.json fallback row is pinned first and
 * editable like any other; every other row expands into an edit dialog
 * (Save writes the one service's file, Delete removes it so the
 * fallback applies again).
 *
 * Data: GET/PUT/DELETE /api/config/service (plain fetch — the shared
 * client cache must never serve a pre-save state on these pages).
 *
 * The edit dialog keeps its fields narrow (the two short ones share a
 * wrap row), each wired to a shared expected-text rule (fieldRules)
 * that validates while typing and blocks Save; Max parallel is a plain
 * text field (no number spinner).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Snackbar,
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
import Refresh from "@mui/icons-material/Refresh";
import {
  deleteServiceConfig,
  fetchServiceConfig,
  saveServiceConfig,
  type ServiceConfigEntry,
  type ServiceConfigSave,
} from "@/lib/api-client/config";
import ConfigServiceTable from "@/config/components/ConfigServiceTable";
import RuleTextField from "@/config/components/RuleTextField";
import {
  DOTTED_NAME,
  FREE_TEXT_OR_NULL,
  INT_GTE1,
  REL_PATH_LIST,
  firstRuleError,
} from "@/config/components/fieldRules";
import { fmtDateTimeShanghai } from "@/shared/datetime";

const FREQ_VALUES = [
  "always",
  "daily",
  "monthly",
  "semiannually",
  "once",
  "occasionally",
  "on_demand",
];

/** Directory filter lists the tree's first two layers only (downloads,
 * downloads.index, …) — deeper dirs are reached via the layer-2 subtree —
 * and presets the filter to the analysis subtree. */
const DIR_FILTER_MAX_DEPTH = 2;
const DIR_FILTER_DEFAULT_DIR = "analysis";

/** Editable form state — strings while typing; blank numeric → NaN
 *  caught by validation on save. */
interface ServiceForm {
  service: string;
  maxParallel: string;
  freq: string;
  schedule: string;
  notes: string;
  sourceRef: string;
}

const EMPTY_FORM: ServiceForm = {
  service: "",
  maxParallel: "1",
  freq: "on_demand",
  schedule: "",
  notes: "",
  sourceRef: "",
};

function toForm(e: ServiceConfigEntry): ServiceForm {
  return {
    service: e.service,
    maxParallel: String(e.maxParallel),
    freq: e.freq,
    schedule: e.schedule ?? "",
    notes: e.notes ?? "",
    sourceRef: e.sourceRef,
  };
}

export default function ServiceConfigPage() {
  const [rows, setRows] = useState<ServiceConfigEntry[] | null>(null);
  const [defaultEntry, setDefaultEntry] = useState<ServiceConfigEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Editing state: `edit` is the row being edited (null = closed), and
  // `isNew` marks the create-new dialog (service name editable).
  const [form, setForm] = useState<ServiceForm | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await fetchServiceConfig();
      setRows(resp.rows);
      setDefaultEntry(resp.default);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openEdit = useCallback(
    (entry: ServiceConfigEntry, asNew: boolean) => {
      setForm(toForm(entry));
      setIsNew(asNew);
      setFormError(null);
    },
    [],
  );

  const knownServices = useMemo(
    () =>
      new Set(
        [...(rows ?? []), defaultEntry]
          .filter((r): r is ServiceConfigEntry => r != null)
          .map((r) => r.service),
      ),
    [rows, defaultEntry],
  );

  const save = useCallback(async () => {
    if (form == null) return;
    const formErr = firstRuleError([
      ["Service", DOTTED_NAME, form.service],
      ["Max parallel", INT_GTE1, form.maxParallel],
      ["Source ref", REL_PATH_LIST, form.sourceRef],
    ]);
    if (formErr != null) {
      setFormError(formErr);
      return;
    }
    if (isNew && knownServices.has(form.service)) {
      setFormError(`A config file for ${form.service} already exists — edit it instead`);
      return;
    }
    const body: ServiceConfigSave = {
      service: form.service.trim(),
      maxParallel: Number(form.maxParallel),
      freq: form.freq,
      schedule: form.schedule === "" ? null : form.schedule,
      notes: form.notes === "" ? null : form.notes,
      sourceRef: form.sourceRef.trim(),
    };
    setSaving(true);
    setFormError(null);
    try {
      await saveServiceConfig(body);
      setToast(`Saved ${body.service}`);
      setForm(null);
      await load();
    } catch (e) {
      setFormError(String(e).replace(/^Error: /, ""));
    } finally {
      setSaving(false);
    }
  }, [form, isNew, knownServices, load]);

  const remove = useCallback(
    async (service: string) => {
      setSaving(true);
      setFormError(null);
      try {
        await deleteServiceConfig(service);
        setToast(`Deleted ${service} — the default fallback now applies`);
        setForm(null);
        await load();
      } catch (e) {
        setFormError(String(e).replace(/^Error: /, ""));
      } finally {
        setSaving(false);
      }
    },
    [load],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">Service Config</Typography>
        <Typography variant="caption" color="text.secondary">
          {rows != null
            ? `${rows.length} services · cap + supposed-run cadence · source: database/sql/monitoring/static/service_config/`
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
        <Button
          size="small"
          variant="outlined"
          startIcon={<Add />}
          onClick={() => {
            setForm({ ...EMPTY_FORM });
            setIsNew(true);
            setFormError(null);
          }}
        >
          New service
        </Button>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <ConfigServiceTable
        name="Services"
        rows={rows}
        searchText={(r) => r.service}
        maxDepth={DIR_FILTER_MAX_DEPTH}
        defaultDir={DIR_FILTER_DEFAULT_DIR}
      >
        {(visible, query) => (
          <>
            <TableHead>
              <TableRow>
                <TableCell>Service</TableCell>
                <TableCell align="right">Max parallel</TableCell>
                <TableCell>Freq</TableCell>
                <TableCell>Schedule</TableCell>
                <TableCell>Notes</TableCell>
                <TableCell>Source</TableCell>
                <TableCell>Updated</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {defaultEntry != null && (
                <TableRow
                  hover
                  sx={{ cursor: "pointer", "& td": { bgcolor: "action.hover" } }}
                  onClick={() => openEdit(defaultEntry, false)}
                >
                  <TableCell sx={{ fontFamily: "monospace" }}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Chip size="small" label="fallback" color="warning" />
                      <Tooltip title="default.json — applied to every service without its own file">
                        <span>default</span>
                      </Tooltip>
                    </Box>
                  </TableCell>
                  <TableCell align="right">{defaultEntry.maxParallel}</TableCell>
                  <TableCell>{defaultEntry.freq}</TableCell>
                  <TableCell>{defaultEntry.schedule ?? "—"}</TableCell>
                  <TableCell>{defaultEntry.notes ?? "—"}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                    {defaultEntry.sourceRef}
                  </TableCell>
                  <TableCell>{fmtDateTimeShanghai(defaultEntry.updatedAt)}</TableCell>
                </TableRow>
              )}
              {visible.map((r) => (
                <TableRow key={r.service} hover sx={{ cursor: "pointer" }} onClick={() => openEdit(r, false)}>
                  <TableCell sx={{ fontFamily: "monospace" }}>{r.service}</TableCell>
                  <TableCell align="right">{r.maxParallel}</TableCell>
                  <TableCell>{r.freq}</TableCell>
                  <TableCell>{r.schedule ?? "—"}</TableCell>
                  <TableCell>{r.notes ?? "—"}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                    {r.sourceRef}
                  </TableCell>
                  <TableCell>{fmtDateTimeShanghai(r.updatedAt)}</TableCell>
                </TableRow>
              ))}
              {rows != null && visible.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} align="center">
                    <Typography variant="caption" color="text.secondary" sx={{ my: 2, display: "inline-block" }}>
                      No service matches “{query}”
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </>
        )}
      </ConfigServiceTable>

      <Dialog open={form != null} onClose={() => setForm(null)} maxWidth="sm" fullWidth>
        <DialogTitle>
          {isNew ? "New service config" : `Edit ${form?.service}`}
        </DialogTitle>
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}>
          {formError && <Alert severity="error">{formError}</Alert>}
          <RuleTextField
            rule={DOTTED_NAME}
            label="Service (dotted name)"
            value={form?.service ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, service: v }))}
            disabled={!isNew}
            hint="dotted name: letters/digits/_/- segments, e.g. builds.etf · the name IS the tree path: builds.etf → builds/etf.json"
            sx={{ fontFamily: "monospace", maxWidth: 480 }}
          />
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "flex-start" }}>
            <RuleTextField
              rule={INT_GTE1}
              label="Max parallel"
              value={form?.maxParallel ?? ""}
              onChange={(v) => setForm((f) => (f == null ? f : { ...f, maxParallel: v }))}
              hint="integer ≥ 1 · registration auto-exits past this cap"
              inputProps={{ inputMode: "numeric" }}
              sx={{ width: 170 }}
            />
            <TextField
              select
              label="Freq"
              value={form?.freq ?? "on_demand"}
              onChange={(e) => setForm((f) => (f == null ? f : { ...f, freq: e.target.value }))}
              sx={{ width: 170 }}
            >
              {FREQ_VALUES.map((f) => (
                <MenuItem key={f} value={f}>
                  {f}
                </MenuItem>
              ))}
            </TextField>
          </Box>
          <RuleTextField
            rule={FREE_TEXT_OR_NULL}
            label="Schedule"
            value={form?.schedule ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, schedule: v }))}
            hint="free text — human-readable timing detail · blank = null"
            sx={{ maxWidth: 480 }}
          />
          <RuleTextField
            rule={FREE_TEXT_OR_NULL}
            label="Notes"
            multiline
            minRows={2}
            value={form?.notes ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, notes: v }))}
            sx={{ maxWidth: 480 }}
          />
          <RuleTextField
            rule={REL_PATH_LIST}
            label="Source ref"
            value={form?.sourceRef ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, sourceRef: v }))}
            sx={{ maxWidth: 480 }}
          />
        </DialogContent>
        <DialogActions>
          {form != null && form.service !== "default" && !isNew && (
            <Button
              color="error"
              startIcon={<Delete />}
              disabled={saving}
              onClick={() => remove(form.service)}
            >
              Delete
            </Button>
          )}
          <Box sx={{ flexGrow: 1 }} />
          <Button color="inherit" onClick={() => setForm(null)}>
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
