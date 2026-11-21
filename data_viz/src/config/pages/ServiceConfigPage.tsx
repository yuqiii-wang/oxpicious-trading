/**
 * Service config tab — read + write the service_config/ static tree
 * (database/sql/monitoring/static/service_config/, one JSON file per
 * service: concurrency cap + cross-service parallel-services lists +
 * the resident flag + the one-time task's start cadence). The
 * default.json fallback row is pinned first and
 * editable like any other; every other row expands into an edit dialog
 * (Save writes the one service's file, Delete removes it so the
 * fallback applies again).
 *
 * Data: GET/PUT/DELETE /api/config/service (plain fetch — the shared
 * client cache must never serve a pre-save state on these pages).
 *
 * The table toolbar filters by directory subtree, freq value (dropdown
 * derived from the rows; residents group under "resident"), and
 * free-text search.
 * * The edit dialog keeps its fields narrow (the two short ones share a
 * wrap row), each wired to a shared expected-text rule (fieldRules)
 * that validates while typing and blocks Save; Max parallel is a plain
 * text field (no number spinner). Max parallel runs is OPTIONAL
 * (blank = null = the code default of one at a time) — the in-service
 * run cap only concurrent-safe daemons define (the deep-agents summary
 * generation today), independent of the heartbeat's Max parallel
 * process cap. The RESIDENT switch splits the fleet: on = an
 * always-live daemon (the resident supervisor / ensure-up path keeps
 * it up; no freq — cadence does not apply); off = a one-time task
 * whose Freq orchestrates when it starts. Priority (blank = derive
 * from resident/freq + domain) is the orchestration give-way rank:
 * higher wins conflicts — a build stops for a download, less frequent
 * gives way to more frequent, a resident outranks daily.
 *
 * The header toggle switches this tab between the PROCESS SERVICES view
 * (everything above — the service_config tree of pipeline processes) and
 * the SERVER CLUSTER view (ClusterServersTable): the resident FastAPI
 * area servers with their heartbeat state, each row expanding into the
 * server's exposed API surface from its FastAPI openapi.json. The
 * cluster view is read-only — server control lives on the Service
 * Monitor page.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
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
  Snackbar,
  Switch,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
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
import ClusterServersTable from "@/config/components/ClusterServersTable";
import ConfigServiceTable from "@/config/components/ConfigServiceTable";
import RuleTextField from "@/config/components/RuleTextField";
import {
  DOTTED_NAME,
  FREE_TEXT_OR_NULL,
  INT_GTE0_OR_NULL,
  INT_GTE1,
  INT_GTE1_OR_NULL,
  REL_PATH_LIST,
  firstRuleError,
} from "@/config/components/fieldRules";
import { fmtDateTimeShanghai } from "@/shared/datetime";

/** The NON-RESIDENT task cadences (the retired "always" labeling is
 *  the Resident switch now — a resident always lives, no freq). */
const FREQ_VALUES = [
  "daily",
  "weekly",
  "monthly",
  "quarterly",
  "semiannually",
  "once",
  "occasionally",
  "on_demand",
];

/** Directory filter lists the tree's first two layers only (downloads,
 *  downloads.index, …) — deeper dirs are reached via the layer-2 subtree —
 *  and presets the filter to the analysis subtree. */
const DIR_FILTER_MAX_DEPTH = 2;
const DIR_FILTER_DEFAULT_DIR = "analysis";

/** One selectable parallel-services entry in the edit dialog's dropdowns:
 *  a scope, a sub-scope path, or a full service name from the loaded
 *  config tree. */
interface ParallelOption {
  entry: string;
  kind: "scope" | "sub-scope" | "service";
}

/** Editable form state — strings while typing; blank numeric → NaN
 * caught by validation on save. The two parallel-services lists are
 * dropdown-selected from the config tree's scopes / sub-scope paths /
 * service names (arrays of entries). isResident on = always-live
 * daemon (freq not applicable — kept but ignored while the switch is
 * on, so toggling back and forth restores the cadence). */
interface ServiceForm {
  service: string;
  maxParallel: string;
  maxParallelRuns: string;
  includedParallel: string[];
  excludedParallel: string[];
  isResident: boolean;
  freq: string;
  priority: string;
  schedule: string;
  notes: string;
  sourceRef: string;
}

const EMPTY_FORM: ServiceForm = {
  service: "",
  maxParallel: "1",
  maxParallelRuns: "",
  includedParallel: [],
  excludedParallel: [],
  isResident: false,
  freq: "on_demand",
  priority: "",
  schedule: "",
  notes: "",
  sourceRef: "",
};

function toForm(e: ServiceConfigEntry): ServiceForm {
  return {
    service: e.service,
    maxParallel: String(e.maxParallel),
    maxParallelRuns: e.maxParallelRuns == null ? "" : String(e.maxParallelRuns),
    includedParallel: e.includedParallelServices,
    excludedParallel: e.excludedParallelServices,
    isResident: e.isResident,
    freq: e.freq ?? "on_demand",
    priority: String(e.priority),
    schedule: e.schedule ?? "",
    notes: e.notes ?? "",
    sourceRef: e.sourceRef,
  };
}

/** Parallel-list table cell: monospace when populated, muted "—" when
 * empty (the rollout default). */
function listCellText(list: string[]): string {
  return list.length > 0 ? list.join(", ") : "—";
}

/** Kind cell: a resident (always lives) gets a chip, a one-time task the
 * muted word. */
function kindCell(isResident: boolean) {
  return isResident ? (
    <Chip size="small" label="resident" color="success" variant="outlined" />
  ) : (
    <Typography variant="caption" color="text.secondary">
      task
    </Typography>
  );
}

/** Freq cell: the task's start cadence — "—" for a resident (cadence
 * does not apply). */
function freqCellText(freq: string | null): string {
  return freq ?? "—";
}

function listCellSx(list: string[]): object {
  return list.length > 0
    ? { fontFamily: "monospace", fontSize: "0.75rem" }
    : { color: "text.disabled" };
}

export default function ServiceConfigPage() {
  const [rows, setRows] = useState<ServiceConfigEntry[] | null>(null);
  const [defaultEntry, setDefaultEntry] = useState<ServiceConfigEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The tab's two views: the pipeline-process config tree (the table +
  // edit dialog below) vs the resident FastAPI area servers (the Server
  // cluster table — heartbeat + exposed API surface per server).
  const [view, setView] = useState<"processes" | "cluster">("processes");
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

  // The parallel-services dropdown options: every dotted prefix (scope /
  // sub-scope path / full name) of every loaded service, classified and
  // grouped — the SAME derivation the API's PUT validates against
  // (validParallelServiceEntries in static-config.service), so a saved
  // list can only ever name real scopes / services. Each dropdown hides
  // what the other list already selected: a service may run in parallel
  // iff it is included AND not excluded — one entry cannot be both.
  const parallelOptions = useMemo<ParallelOption[]>(() => {
    const byEntry = new Map<string, ParallelOption>();
    const services = (rows ?? []).map((r) => r.service);
    // Kind priority on the (impossible-in-practice) overlap: a full
    // service name wins, then 1-segment scope, then sub-scope path.
    const fullNames = new Set(services);
    for (const service of services) {
      const segments = service.split(".");
      for (let i = 1; i <= segments.length; i++) {
        const entry = segments.slice(0, i).join(".");
        if (byEntry.has(entry)) continue;
        byEntry.set(entry, {
          entry,
          kind: fullNames.has(entry)
            ? "service"
            : i === 1
              ? "scope"
              : "sub-scope",
        });
      }
    }
    const rank = (k: ParallelOption["kind"]) =>
      k === "scope" ? 0 : k === "sub-scope" ? 1 : 2;
    return [...byEntry.values()].sort(
      (a, b) => rank(a.kind) - rank(b.kind) || a.entry.localeCompare(b.entry),
    );
  }, [rows]);
  const includedOptions = useMemo(
    () =>
      parallelOptions.filter((o) => !form?.excludedParallel.includes(o.entry)),
    [parallelOptions, form?.excludedParallel],
  );
  const excludedOptions = useMemo(
    () =>
      parallelOptions.filter((o) => !form?.includedParallel.includes(o.entry)),
    [parallelOptions, form?.includedParallel],
  );

  const save = useCallback(async () => {
    if (form == null) return;
    const formErr = firstRuleError([
      ["Service", DOTTED_NAME, form.service],
      ["Max parallel", INT_GTE1, form.maxParallel],
      ["Max parallel runs", INT_GTE1_OR_NULL, form.maxParallelRuns],
      ["Priority", INT_GTE0_OR_NULL, form.priority],
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
      maxParallelRuns:
        form.maxParallelRuns.trim() === "" ? null : Number(form.maxParallelRuns),
      includedParallelServices: form.includedParallel,
      excludedParallelServices: form.excludedParallel,
      isResident: form.isResident,
      // A resident carries no freq — cadence does not apply (it always
      // lives); the server rejects a non-null freq on a resident.
      freq: form.isResident ? null : form.freq,
      priority: form.priority.trim() === "" ? null : Number(form.priority),
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
        <ToggleButtonGroup
          size="small"
          exclusive
          value={view}
          onChange={(_, v) => {
            if (v != null) setView(v);
          }}
          aria-label="service config view"
        >
          <ToggleButton value="processes">Process services</ToggleButton>
          <ToggleButton value="cluster">Server cluster</ToggleButton>
        </ToggleButtonGroup>
        {view === "processes" && (
          <Typography variant="caption" color="text.secondary">
            {rows != null
              ? `${rows.length} services · process cap + parallel-runs cap + parallel-run lists + resident flag / start cadence · source: database/sql/monitoring/static/service_config/`
              : "loading…"}
          </Typography>
        )}
        <Box sx={{ flexGrow: 1 }} />
        {view === "processes" && (
          <>
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
          </>
        )}
      </Box>

      {error && view === "processes" && <Alert severity="error">{error}</Alert>}

      {view === "cluster" && <ClusterServersTable />}

      {view === "processes" && (
      <ConfigServiceTable
        name="Services"
        rows={rows}
        searchText={(r) => r.service}
        filters={[
          {
            label: "Kind / freq",
            // Residents group under "resident" — an always-live service
            // has no cadence.
            rowValue: (r) => (r.isResident ? "resident" : r.freq),
          },
        ]}
        maxDepth={DIR_FILTER_MAX_DEPTH}
        defaultDir={DIR_FILTER_DEFAULT_DIR}
      >
        {(visible, query) => (
          <>
            <TableHead>
              <TableRow>
                <TableCell>Service</TableCell>
                <TableCell align="right">Max parallel</TableCell>
                <TableCell align="right">Max parallel runs</TableCell>
                <TableCell>Included parallel</TableCell>
                <TableCell>Excluded parallel</TableCell>
                <TableCell>Kind</TableCell>
                <TableCell>Freq</TableCell>
                <TableCell align="right">Priority</TableCell>
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
                  <TableCell align="right" sx={{ color: defaultEntry.maxParallelRuns == null ? "text.disabled" : undefined }}>
                    {defaultEntry.maxParallelRuns ?? "—"}
                  </TableCell>
                  <TableCell sx={listCellSx(defaultEntry.includedParallelServices)}>
                    {listCellText(defaultEntry.includedParallelServices)}
                  </TableCell>
                  <TableCell sx={listCellSx(defaultEntry.excludedParallelServices)}>
                    {listCellText(defaultEntry.excludedParallelServices)}
                  </TableCell>
                  <TableCell>{kindCell(defaultEntry.isResident)}</TableCell>
                  <TableCell sx={defaultEntry.freq == null ? { color: "text.disabled" } : undefined}>
                    {freqCellText(defaultEntry.freq)}
                  </TableCell>
                  <TableCell align="right">{defaultEntry.priority}</TableCell>
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
                  <TableCell align="right" sx={{ color: r.maxParallelRuns == null ? "text.disabled" : undefined }}>
                    {r.maxParallelRuns ?? "—"}
                  </TableCell>
                  <TableCell sx={listCellSx(r.includedParallelServices)}>
                    {listCellText(r.includedParallelServices)}
                  </TableCell>
                  <TableCell sx={listCellSx(r.excludedParallelServices)}>
                    {listCellText(r.excludedParallelServices)}
                  </TableCell>
                  <TableCell>{kindCell(r.isResident)}</TableCell>
                  <TableCell sx={r.freq == null ? { color: "text.disabled" } : undefined}>
                    {freqCellText(r.freq)}
                  </TableCell>
                  <TableCell align="right">{r.priority}</TableCell>
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
                  <TableCell colSpan={12} align="center">
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
      )}

      <Dialog
        open={form != null}
        onClose={() => setForm(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {isNew ? "New service config" : `Edit ${form?.service}`}
        </DialogTitle>
        <DialogContent
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            pt: 1,
            // The dialog grows with its fields — cap it at the viewport
            // and scroll instead of clipping the first fields.
            maxHeight: "68vh",
            overflowY: "auto",
          }}
        >
          {formError && <Alert severity="error">{formError}</Alert>}
          <RuleTextField
            rule={DOTTED_NAME}
            label="Service (dotted name)"
            value={form?.service ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, service: v }))}
            disabled={!isNew}
            hint="dotted name: letters/digits/_/- segments, e.g. builds.etf · the name IS the tree path: builds.etf → builds/etf.json"
            sx={{ fontFamily: "monospace" }}
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
            <RuleTextField
              rule={INT_GTE1_OR_NULL}
              label="Max parallel runs"
              value={form?.maxParallelRuns ?? ""}
              onChange={(v) => setForm((f) => (f == null ? f : { ...f, maxParallelRuns: v }))}
              hint="optional · in-service run cap for a concurrent-safe daemon (deep-agents summary generation) · blank = null (one at a time)"
              inputProps={{ inputMode: "numeric" }}
              sx={{ width: 170 }}
            />
            <FormControlLabel
              control={
                <Switch
                  checked={form?.isResident ?? false}
                  onChange={(e) =>
                    setForm((f) => (f == null ? f : { ...f, isResident: e.target.checked }))
                  }
                />
              }
              label="Resident"
              sx={{ width: 170, mt: 0.5 }}
              slotProps={{
                typography: {
                  variant: "body2",
                  sx: { whiteSpace: "nowrap" },
                },
              }}
            />
            {form?.isResident ? (
              <Tooltip title="an always-live service has no cadence — turn Resident off to set one">
                <TextField
                  disabled
                  label="Freq"
                  value="always live"
                  sx={{ width: 170 }}
                />
              </Tooltip>
            ) : (
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
            )}
            <RuleTextField
              rule={INT_GTE0_OR_NULL}
              label="Priority"
              value={form?.priority ?? ""}
              onChange={(v) => setForm((f) => (f == null ? f : { ...f, priority: v }))}
              hint="orchestration give-way rank · higher wins conflicts (a build stops for a download; less frequent gives way; a resident outranks daily) · blank = derive from resident/freq + domain"
              inputProps={{ inputMode: "numeric" }}
              sx={{ width: 170 }}
            />
          </Box>
          <Autocomplete
            multiple
            options={includedOptions}
            groupBy={(o) => o.kind}
            getOptionLabel={(o) => o.entry}
            isOptionEqualToValue={(o, v) => o.entry === v.entry}
            value={parallelOptions.filter((o) =>
              form?.includedParallel.includes(o.entry),
            )}
            onChange={(_, values) =>
              setForm((f) =>
                f == null ? f : { ...f, includedParallel: values.map((v) => v.entry) },
              )
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Included parallel services"
                helperText="whitelist of parallel-run companions — runs alongside these AND not the excluded ones · empty = unrestricted"
              />
            )}
            renderGroup={(params) => (
              <li key={params.key}>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ pl: 2, textTransform: "uppercase" }}
                  component="div"
                >
                  {params.group}
                </Typography>
                <ul style={{ margin: 0, padding: 0 }}>{params.children}</ul>
              </li>
            )}
          />
          <Autocomplete
            multiple
            options={excludedOptions}
            groupBy={(o) => o.kind}
            getOptionLabel={(o) => o.entry}
            isOptionEqualToValue={(o, v) => o.entry === v.entry}
            value={parallelOptions.filter((o) =>
              form?.excludedParallel.includes(o.entry),
            )}
            onChange={(_, values) =>
              setForm((f) =>
                f == null ? f : { ...f, excludedParallel: values.map((v) => v.entry) },
              )
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Excluded parallel services"
                helperText="may NOT run in parallel with — wins over a less-specific inclusion (name > sub-scope > scope) · empty = no exclusions"
              />
            )}
            renderGroup={(params) => (
              <li key={params.key}>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ pl: 2, textTransform: "uppercase" }}
                  component="div"
                >
                  {params.group}
                </Typography>
                <ul style={{ margin: 0, padding: 0 }}>{params.children}</ul>
              </li>
            )}
          />
          <RuleTextField
            rule={FREE_TEXT_OR_NULL}
            label="Schedule"
            value={form?.schedule ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, schedule: v }))}
            hint="free text — human-readable timing detail · blank = null"
          />
          <RuleTextField
            rule={FREE_TEXT_OR_NULL}
            label="Notes"
            multiline
            minRows={2}
            value={form?.notes ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, notes: v }))}
          />
          <RuleTextField
            rule={REL_PATH_LIST}
            label="Source ref"
            value={form?.sourceRef ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, sourceRef: v }))}
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
