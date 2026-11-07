/**
 * Secrets tab — the Config page's credential registry
 * (static.secret_config, DB-backed: type + provider + model/engine +
 * endpoint + the API KEY, encrypted at rest). Rows are consumed at
 * STARTUP — the deep agent reads its chat config per run
 * (llm_agents/deep_agents/llm_config.py); every BaseLlmProvider's key
 * falls back to this registry after the env chain
 * (llm_agents/_core/secret_config.py — successor of the deleted root
 * .env keys), so a save applies to the next consumer start.
 *
 * Data: GET/POST/PUT /api/config/secrets + POST /secrets/test (plain
 * fetch — the shared client cache must never serve a pre-save state).
 *
 * Types (secret_type, mirror of static.label_dim 'secret_type'): llm
 * (chat credentials — provider domain ext_llm_provider, model
 * REQUIRED) and web_search (search credentials — provider domain
 * ext_http_source, model = the engine, optional).
 *
 * Secret handling: the API key never crosses back to the client — the
 * table shows the masked hint (•••••••• + last 4) only, and the edit
 * dialog's password field is POPULATED WITH A DISPLAY MASK (pure
 * dots, never the secret) when a key is stored; clicking the field
 * clears it for typing a replacement, and saving the mask unchanged
 * keeps the stored key. Removing a credential is the row's ✕ action
 * (confirm-guarded; the encrypted key goes with the row).
 *
 * The table reuses the shared config shell (search + column filters;
 * the dotted-dir filter is off — these rows don't key on a tree path),
 * with rows adapted under the shell's `service` field as
 * "type / provider / model".
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
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
import Bolt from "@mui/icons-material/Bolt";
import CloseIcon from "@mui/icons-material/Close";
import Refresh from "@mui/icons-material/Refresh";
import {
  createSecret,
  deleteSecret,
  fetchSecrets,
  testSecret,
  updateSecret,
  type SecretEntry,
  type SecretSaveBody,
  type SecretTestResult,
  type SecretType,
} from "@/lib/api-client/config";
import ConfigServiceTable from "@/config/components/ConfigServiceTable";
import RuleTextField from "@/config/components/RuleTextField";
import {
  FREE_TEXT_OR_NULL,
  HTTP_URL_OR_NULL,
  INT_GTE1_OR_NULL,
  REQUIRED_TEXT,
  firstRuleError,
  numOrNull,
  urlOrNull,
} from "@/config/components/fieldRules";
import { fmtDateTimeShanghai } from "@/shared/datetime";

/** Provider options per secret_type — local mirrors of the three label
 *  sources of truth (database/sql/static/label_dim/
 *  06_monitoring_infra.sql, _common/labels.py, api label-codes.ts).
 *  The provider domain is SELECTED BY the type (the audit pattern):
 *  llm → ext_llm_provider, web_search → ext_http_source. The API
 *  re-validates via encodeLabelParam, so an out-of-mirror pick is a
 *  400, not a bad row. */
const PROVIDER_OPTIONS: Record<SecretType, Array<{ code: number; label: string }>> = {
  llm: [
    { code: 0, label: "zhipu" },
    { code: 99, label: "other" },
  ],
  web_search: [
    { code: 15, label: "zhipu" },
    { code: 13, label: "zhihu" },
    { code: 99, label: "other" },
  ],
};

/** The type-select's options with the human hints. */
const TYPE_OPTIONS: Array<{ value: SecretType; hint: string }> = [
  { value: "llm", hint: "chat credentials (deep agent, Ask AI)" },
  { value: "web_search", hint: "search credentials (online search providers)" },
];

/** The edit field's DISPLAY value while a key is stored — pure mask,
 *  never the secret (the plaintext never leaves the server). Clicking
 *  the field clears it for typing a new key; saving the mask unchanged
 *  (or empty) keeps the stored key — the mask is never sent. */
const API_KEY_MASK = "••••••••••••";

/** Editable form state — strings while typing; blank numeric → null on
 *  save. `apiKey` is the typed plaintext ("" / mask = keep). */
interface SecretForm {
  secretType: SecretType;
  provider: string;
  modelName: string;
  contextLength: string;
  apiUrl: string;
  apiKey: string;
  isActive: boolean;
  notes: string;
}

function emptyForm(type: SecretType): SecretForm {
  return {
    secretType: type,
    provider: PROVIDER_OPTIONS[type][0].label,
    modelName: "",
    contextLength: "",
    apiUrl: "",
    apiKey: "",
    isActive: true,
    notes: "",
  };
}

function toForm(e: SecretEntry): SecretForm {
  return {
    secretType: e.secretType,
    provider: e.provider,
    modelName: e.modelName ?? "",
    contextLength: e.contextLength == null ? "" : String(e.contextLength),
    apiUrl: e.apiUrl ?? "",
    apiKey: e.hasApiKey ? API_KEY_MASK : "",
    isActive: e.isActive,
    notes: e.notes ?? "",
  };
}

/** The connection-test panel's state (per dialog opening). */
interface TestState {
  busy: boolean;
  result: SecretTestResult | null;
  error: string | null;
}

const IDLE_TEST: TestState = { busy: false, result: null, error: null };

export default function SecretsPage() {
  const [rows, setRows] = useState<SecretEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Editing state: `edit` is the row being edited (null = closed /
  // creating), `isNew` marks the create-new dialog.
  const [edit, setEdit] = useState<SecretEntry | null>(null);
  const [form, setForm] = useState<SecretForm | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [test, setTest] = useState<TestState>(IDLE_TEST);
  // The row pending ✕ deletion (confirm dialog open while non-null).
  const [deleteTarget, setDeleteTarget] = useState<SecretEntry | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    try {
      const resp = await fetchSecrets();
      setRows(resp.rows);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openEdit = useCallback((entry: SecretEntry) => {
    setEdit(entry);
    setForm(toForm(entry));
    setIsNew(false);
    setFormError(null);
    setTest(IDLE_TEST);
  }, []);

  // The type switch resets the provider to the new domain's first
  // option (the two types draw from different label domains).
  const setType = useCallback((type: SecretType) => {
    setForm((f) =>
      f == null || f.secretType === type
        ? f
        : { ...f, secretType: type, provider: PROVIDER_OPTIONS[type][0].label },
    );
  }, []);

  const remove = useCallback(
    async (entry: SecretEntry) => {
      setDeleting(true);
      try {
        await deleteSecret(entry.secretId);
        setToast(`Deleted ${entry.secretType} / ${entry.provider}${entry.modelName ? ` / ${entry.modelName}` : ""}`);
        setDeleteTarget(null);
        if (edit?.secretId === entry.secretId) {
          setForm(null);
          setEdit(null);
          setTest(IDLE_TEST);
        }
        await load();
      } catch (e) {
        setFormError(String(e).replace(/^Error: /, ""));
        setDeleteTarget(null);
      } finally {
        setDeleting(false);
      }
    },
    [edit, load],
  );

  const save = useCallback(async () => {
    if (form == null) return;
    const formErr = firstRuleError([
      ["Model / engine", form.secretType === "llm" ? REQUIRED_TEXT : FREE_TEXT_OR_NULL, form.modelName],
      ["Context length", INT_GTE1_OR_NULL, form.contextLength],
      ["API URL", HTTP_URL_OR_NULL, form.apiUrl],
    ]);
    if (formErr != null) {
      setFormError(formErr);
      return;
    }
    const identityKey = `${form.secretType} / ${form.provider} / ${form.modelName.trim() || "(no model)"}`;
    if (
      isNew &&
      (rows ?? []).some(
        (r) =>
          r.secretType === form.secretType &&
          r.provider === form.provider &&
          (r.modelName ?? "") === form.modelName.trim(),
      )
    ) {
      setFormError(`A secret for ${identityKey} already exists — edit it instead`);
      return;
    }
    // The secret bi-state → the wire: the display mask (unchanged) or
    // empty = omitted (keep / none on create); typed text = replace.
    // The mask is NEVER sent — the server would otherwise encrypt it
    // as the new key. (Removing a key entirely is the row's ✕ delete.)
    const typedKey = form.apiKey.trim();
    const apiKey: string | null | undefined =
      typedKey === "" || typedKey === API_KEY_MASK ? undefined : typedKey;
    const body: SecretSaveBody = {
      secretType: form.secretType,
      provider: form.provider,
      modelName: form.modelName.trim() === "" ? null : form.modelName.trim(),
      contextLength: numOrNull(form.contextLength),
      apiUrl: urlOrNull(form.apiUrl),
      apiKey,
      isActive: form.isActive,
      notes: form.notes === "" ? null : form.notes,
    };
    setSaving(true);
    setFormError(null);
    try {
      const saved =
        isNew || edit == null
          ? await createSecret(body)
          : await updateSecret(edit.secretId, body);
      setToast(`Saved ${saved.entry.secretType} / ${saved.entry.provider}`);
      setForm(null);
      setEdit(null);
      setTest(IDLE_TEST);
      await load();
    } catch (e) {
      setFormError(String(e).replace(/^Error: /, ""));
    } finally {
      setSaving(false);
    }
  }, [form, isNew, edit, rows, load]);

  // One real minimal request per type with the values AS TYPED — the
  // stored key / .env fallback fills in an empty (or still-masked) key
  // field. The mask itself is never sent: omitted means "use the
  // stored key".
  const runTest = useCallback(async () => {
    if (form == null) return;
    const formErr = firstRuleError([
      ["Model / engine", form.secretType === "llm" ? REQUIRED_TEXT : FREE_TEXT_OR_NULL, form.modelName],
      ["API URL", HTTP_URL_OR_NULL, form.apiUrl],
    ]);
    if (formErr != null) {
      setFormError(formErr);
      return;
    }
    const typedKey = form.apiKey.trim();
    setTest({ busy: true, result: null, error: null });
    try {
      const resp = await testSecret({
        secretId: isNew ? undefined : (edit?.secretId ?? undefined),
        secretType: form.secretType,
        provider: form.provider,
        modelName: form.modelName.trim() === "" ? null : form.modelName.trim(),
        apiUrl: urlOrNull(form.apiUrl),
        apiKey:
          typedKey === "" || typedKey === API_KEY_MASK ? undefined : typedKey,
      });
      setTest({ busy: false, result: resp.result, error: null });
    } catch (e) {
      setTest({ busy: false, result: null, error: String(e).replace(/^Error: /, "") });
    }
  }, [form, isNew, edit]);

  // Table rows adapted to the shared shell's `service` key.
  const tableRows = useMemo(
    () =>
      (rows ?? []).map((r) => ({
        ...r,
        service: `${r.secretType} / ${r.provider} / ${r.modelName ?? "(no model)"}`,
      })),
    [rows],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">Secrets</Typography>
        <Typography variant="caption" color="text.secondary">
          {rows != null
            ? `${rows.length} credentials · type + provider + model/engine + endpoint + API key (encrypted at rest, masked here) · source: static.secret_config — read by the Python consumers at startup`
            : "loading…"}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title="Reload from the database">
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
            setEdit(null);
            setForm(emptyForm("llm"));
            setIsNew(true);
            setFormError(null);
            setTest(IDLE_TEST);
          }}
        >
          New secret
        </Button>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <ConfigServiceTable
        name="Credentials"
        rows={tableRows}
        searchText={(r) => `${r.secretType} ${r.provider} ${r.modelName ?? ""} ${r.apiUrl ?? ""}`}
        filters={[
          { label: "Type", rowValue: (r) => r.secretType },
          { label: "Provider", rowValue: (r) => r.provider },
        ]}
        showDirFilter={false}
        searchPlaceholder="Search type / provider / model / url…"
      >
        {(visible, query) => (
          <>
            <TableHead>
              <TableRow>
                <TableCell>Type</TableCell>
                <TableCell>Provider</TableCell>
                <TableCell>Model / engine</TableCell>
                <TableCell align="right">Context length</TableCell>
                <TableCell>API URL</TableCell>
                <TableCell>API key</TableCell>
                <TableCell>Active</TableCell>
                <TableCell>Notes</TableCell>
                <TableCell>Updated</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map((r) => (
                <TableRow key={r.secretId} hover sx={{ cursor: "pointer" }} onClick={() => openEdit(r)}>
                  <TableCell>
                    <Chip
                      size="small"
                      label={r.secretType}
                      color={r.secretType === "llm" ? "primary" : "secondary"}
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell>{r.provider}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace", color: r.modelName == null ? "text.disabled" : undefined }}>
                    {r.modelName ?? "—"}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{ color: r.contextLength == null ? "text.disabled" : undefined }}
                  >
                    {r.contextLength ?? "—"}
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                    {r.apiUrl ?? "—"}
                  </TableCell>
                  <TableCell>
                    {r.hasApiKey ? (
                      <Tooltip title="Stored encrypted (AES-256-GCM); the plaintext is never returned">
                        <Chip size="small" label={r.apiKeyHint} sx={{ fontFamily: "monospace" }} />
                      </Tooltip>
                    ) : (
                      <Typography variant="caption" color="text.disabled">
                        none
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    {r.isActive ? (
                      <Chip size="small" label="active" color="success" />
                    ) : (
                      <Chip size="small" label="retired" color="default" />
                    )}
                  </TableCell>
                  <TableCell>{r.notes ?? "—"}</TableCell>
                  <TableCell>{fmtDateTimeShanghai(r.updatedAt)}</TableCell>
                  <TableCell align="right">
                    <Tooltip title={`Delete ${r.secretType} / ${r.provider}${r.modelName ? ` / ${r.modelName}` : ""} — the stored key goes with the row`}>
                      <span>
                        <IconButton
                          size="small"
                          aria-label="delete"
                          color="error"
                          // The row itself opens the edit dialog on
                          // click — the ✕ must not bubble into it.
                          onClick={(e) => {
                            e.stopPropagation();
                            setDeleteTarget(r);
                          }}
                        >
                          <CloseIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
              {rows != null && visible.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} align="center">
                    <Typography variant="caption" color="text.secondary" sx={{ my: 2, display: "inline-block" }}>
                      {rows.length === 0
                        ? "No credential rows yet — add one (providers fall back to their built-in constants)"
                        : `No secret matches “${query}”`}
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
        onClose={() => {
          setForm(null);
          setEdit(null);
        }}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {isNew
            ? "New secret"
            : `Edit ${edit?.secretType} / ${edit?.provider}${edit?.modelName ? ` / ${edit.modelName}` : ""}`}
        </DialogTitle>
        <DialogContent
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            // Full padding override — MUI's DialogContent `padding`
            // shorthand out-cascades a bare pt (component styles inject
            // after sx), so the override needs the class-qualified
            // selector. The 20px top gives the first row's floating
            // outlined labels (they stick ~10px above the inputs) room
            // to render.
            "&.MuiDialogContent-root": { p: "20px 24px" },
            // The dialog grows with its fields — cap it at the viewport
            // and scroll instead of clipping the first fields.
            maxHeight: "68vh",
            overflowY: "auto",
          }}
        >
          {formError && <Alert severity="error">{formError}</Alert>}
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "flex-start" }}>
            <TextField
              select
              label="Type"
              value={form?.secretType ?? "llm"}
              onChange={(e) => setType(e.target.value as SecretType)}
              helperText={TYPE_OPTIONS.find((t) => t.value === form?.secretType)?.hint}
              disabled={!isNew}
              sx={{ width: 190 }}
            >
              {TYPE_OPTIONS.map((t) => (
                <MenuItem key={t.value} value={t.value}>
                  {t.value}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="Provider"
              value={form?.provider ?? ""}
              onChange={(e) => setForm((f) => (f == null ? f : { ...f, provider: e.target.value }))}
              helperText={
                form?.secretType === "llm"
                  ? "ext_llm_provider label (shares the request-audit vocabulary)"
                  : "ext_http_source label (shares the request-audit vocabulary)"
              }
              disabled={!isNew}
              sx={{ width: 170 }}
            >
              {(PROVIDER_OPTIONS[form?.secretType ?? "llm"]).map((p) => (
                <MenuItem key={p.code} value={p.label}>
                  {p.label}
                </MenuItem>
              ))}
            </TextField>
            <RuleTextField
              rule={form?.secretType === "llm" ? REQUIRED_TEXT : FREE_TEXT_OR_NULL}
              label={form?.secretType === "llm" ? "Model name" : "Search engine"}
              value={form?.modelName ?? ""}
              onChange={(v) => setForm((f) => (f == null ? f : { ...f, modelName: v }))}
              hint={
                form?.secretType === "llm"
                  ? "the provider's model id, e.g. glm-5.3-flash"
                  : "the search engine, e.g. search_pro · blank = none (zhihu names none)"
              }
              disabled={!isNew || (form?.secretType === "web_search" && form.provider === "zhihu")}
              sx={{ width: 220, fontFamily: "monospace" }}
            />
            <RuleTextField
              rule={INT_GTE1_OR_NULL}
              label="Context length"
              value={form?.contextLength ?? ""}
              onChange={(v) => setForm((f) => (f == null ? f : { ...f, contextLength: v }))}
              hint="llm only · integer ≥ 1 · max context window in tokens · blank = unspecified"
              inputProps={{ inputMode: "numeric" }}
              sx={{ width: 190 }}
            />
          </Box>
          <RuleTextField
            rule={HTTP_URL_OR_NULL}
            label="API URL"
            value={form?.apiUrl ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, apiUrl: v }))}
            hint="the credential's endpoint · blank = the consumer's built-in default"
            sx={{ fontFamily: "monospace" }}
          />
          <RuleTextField
            rule={FREE_TEXT_OR_NULL}
            label="API key"
            type="password"
            autoComplete="new-password"
            value={form?.apiKey ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, apiKey: v }))}
            // The display mask clears on the first click so a new key
            // can be typed; it is never submitted (save treats it as
            // "keep the stored key").
            onClick={() =>
              setForm((f) =>
                f == null || f.apiKey !== API_KEY_MASK ? f : { ...f, apiKey: "" },
              )
            }
            hint={
              isNew
                ? "leave blank = no key · the value is encrypted at rest and only ever shown as ••••••••last4"
                : edit?.hasApiKey
                  ? "dots = the stored key (the plaintext never leaves the server) · click the field to clear it, then type a replacement · leave as-is to keep"
                  : "leave blank = no key · type one to store it (encrypted at rest, shown only as ••••••••last4)"
            }
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={form?.isActive === true}
                onChange={(e) =>
                  setForm((f) => (f == null ? f : { ...f, isActive: e.target.checked }))
                }
              />
            }
            label="Active (the startup loaders only read active rows)"
          />
          <RuleTextField
            rule={FREE_TEXT_OR_NULL}
            label="Notes"
            multiline
            minRows={2}
            value={form?.notes ?? ""}
            onChange={(v) => setForm((f) => (f == null ? f : { ...f, notes: v }))}
          />
          <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<Bolt />}
              disabled={saving || test.busy}
              onClick={runTest}
            >
              {test.busy ? "Testing…" : "Test connection"}
            </Button>
            <Typography variant="caption" color="text.secondary">
              one real request per type (chat ping / 1-hit search) with the values as typed · an empty key field uses the stored key, else the root .env fallback
            </Typography>
          </Box>
          {test.result != null &&
            (test.result.ok ? (
              <Alert severity="success">
                Connection OK · HTTP {test.result.statusCode} · {test.result.latencyMs} ms ·
                key: {test.result.keySource}
                {test.result.replySnippet
                  ? ` · ${JSON.stringify(test.result.replySnippet)}`
                  : " · (no content returned)"}
              </Alert>
            ) : (
              <Alert severity="error">
                Test failed — {test.result.error ?? "unknown error"}
                {test.result.statusCode != null ? ` (HTTP ${test.result.statusCode})` : ""}
              </Alert>
            ))}
          {test.error != null && <Alert severity="error">{test.error}</Alert>}
        </DialogContent>
        <DialogActions>
          <Box sx={{ flexGrow: 1 }} />
          <Button
            color="inherit"
            onClick={() => {
              setForm(null);
              setEdit(null);
              setTest(IDLE_TEST);
            }}
          >
            Cancel
          </Button>
          <Button variant="contained" disabled={saving} onClick={save}>
            Save
          </Button>
        </DialogActions>
      </Dialog>

      {/* The ✕ delete's confirm gate — a credential row goes entirely
          (encrypted key included), so the click names what it removes. */}
      <Dialog
        open={deleteTarget != null}
        onClose={() => setDeleteTarget(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Delete secret</DialogTitle>
        <DialogContent>
          <Typography>
            Delete{" "}
            <Box component="span" sx={{ fontFamily: "monospace" }}>
              {deleteTarget?.secretType} / {deleteTarget?.provider}
              {deleteTarget?.modelName ? ` / ${deleteTarget.modelName}` : ""}
            </Box>
            ? The stored API key ({deleteTarget?.apiKeyHint ?? "none"}) is
            removed with the row and cannot be recovered.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setDeleteTarget(null)}>
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            disabled={deleting}
            startIcon={<CloseIcon />}
            onClick={() => deleteTarget != null && remove(deleteTarget)}
          >
            Delete
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
