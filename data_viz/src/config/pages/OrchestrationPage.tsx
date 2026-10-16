/**
 * Orchestration tab — read-only display of the orchestration step
 * files (scripts/orchestration/, one JSON per tier script:
 * <tier>.json ↔ scripts/<tier>.sh; scripts/run_steps.sh executes each
 * file's steps one by one in key order) plus UI-triggered runs. Every
 * tier's steps consolidate into ONE flat table — rows in main_run.sh
 * tier run order, then step number — styled after the External Request
 * Config table: the shared ConfigServiceTable shell. The toolbar
 * filters by the step's JSON FILE (builds_daily.json, … — the tier
 * file the runner reads; preselected to downloads_daily.json on mount)
 * and by the tier's Freq suffix, plus free-text
 * search; a row's `service` is the step's python -m script (the dotted
 * module name the service_config tree keys on) feeding the search and
 * row identity — the dotted-directory dropdown stays hidden since
 * steps don't key on a directory tree.
 *
 * Runs: each row's first column has a Run button (that one step), and
 * a title-level Run button (enabled once the JSON-file filter selects
 * one tier) sits after the table name — both open the shared
 * RunOrchestrationDialog (confirm + opt-in redirect to the Monitor
 * Services page, where Pipeline processes picks the run up via its
 * heartbeat). Spawns go through the monitor API: POST
 * /api/monitor/stream/run for a step, POST
 * /api/monitor/orchestration/run (run_steps.sh) for a tier.
 *
 * Data: GET /api/config/orchestration (plain fetch — the shared client
 * cache must never serve a pre-save state on these pages). The step
 * files themselves stay git-reviewed edits: no config write UI.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Snackbar,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import PlayArrow from "@mui/icons-material/PlayArrow";
import Refresh from "@mui/icons-material/Refresh";
import {
  fetchOrchestrationConfig,
  type OrchestrationTier,
} from "@/lib/api-client/config";
import ConfigServiceTable from "@/config/components/ConfigServiceTable";
import RunOrchestrationDialog, {
  type OrchestrationRunTarget,
} from "@/config/pages/OrchestrationRunDialog";

/** One flattened table row — a step of one tier. `service` is the
 *  step's python -m script; `file` the tier's orchestration JSON file;
 *  `freq` the tier's cadence suffix. */
interface StepRow {
  service: string;
  tier: string;
  step: number;
  args: string[];
  canBeParallel: boolean;
  note: string | null;
  file: string;
  freq: string;
}

/** JSON-file filter preselected on mount (the busiest tier) — dropped
 *  back to "(all)" by the shell's staleness rule if the file goes away. */
const DEFAULT_FILE_FILTER = "downloads_daily.json";

export default function OrchestrationPage() {
  const [tiers, setTiers] = useState<OrchestrationTier[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The active JSON-file filter selection (null = "(all)") — gates the
  // title-level tier Run button.
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [runTarget, setRunTarget] = useState<OrchestrationRunTarget | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await fetchOrchestrationConfig();
      setTiers(resp.tiers);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Flatten tier run order → step order (both sorted server-side).
  const rows = useMemo<StepRow[]>(
    () =>
      (tiers ?? []).flatMap((t) =>
        t.steps.map((s) => ({
          service: s.script,
          tier: t.tier,
          step: s.step,
          args: s.args,
          canBeParallel: s.canBeParallel,
          note: s.note,
          file: `${t.tier}.json`,
          freq: t.freq,
        })),
      ),
    [tiers],
  );

  const onStarted = useCallback(
    (message: string) => {
      setToast(message);
    },
    [],
  );

  const openTierRun = useCallback(
    (file: string) => {
      const t = (tiers ?? []).find((x) => `${x.tier}.json` === file);
      if (t == null) return;
      setRunTarget({
        kind: "tier",
        tier: t.tier,
        file,
        steps: t.steps.length,
        script: "",
        args: [],
      });
    },
    [tiers],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">Orchestration</Typography>
        <Typography variant="caption" color="text.secondary">
          {tiers != null
            ? `${tiers.length} tiers · ${rows.length} steps in main_run.sh run order · source: scripts/orchestration/`
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
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <ConfigServiceTable
        name="Orchestration Steps"
        rows={rows}
        searchText={(r) => `${r.tier} ${r.service} ${r.args.join(" ")} ${r.note ?? ""}`}
        filters={[
          { label: "JSON file", rowValue: (r) => r.file, width: 250 },
          { label: "Freq", rowValue: (r) => r.freq },
        ]}
        defaultFilters={{ "JSON file": DEFAULT_FILE_FILTER }}
        showDirFilter={false}
        searchPlaceholder="Search tier / script / note…"
        action={
          <Tooltip
            title={
              selectedFile != null
                ? `Run every step of ${selectedFile}, serially`
                : "Select one JSON file in the filter to enable the tier run"
            }
          >
            <span>
              <Button
                size="small"
                variant="outlined"
                startIcon={<PlayArrow fontSize="small" />}
                disabled={selectedFile == null}
                onClick={() => selectedFile != null && openTierRun(selectedFile)}
              >
                Run
              </Button>
            </span>
          </Tooltip>
        }
        onActiveFiltersChange={(active) =>
          setSelectedFile(active["JSON file"] !== "" ? active["JSON file"] : null)
        }
      >
        {(visible, query) => (
          <>
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: 56 }}>Run</TableCell>
                <TableCell sx={{ minWidth: 170 }}>Tier</TableCell>
                <TableCell align="right" sx={{ width: 40 }}>#</TableCell>
                <TableCell sx={{ minWidth: 220 }}>Script</TableCell>
                <TableCell sx={{ width: 150 }}>Args</TableCell>
                <TableCell sx={{ width: 90 }}>Mode</TableCell>
                <TableCell>Note</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map((r) => (
                <TableRow key={`${r.tier}#${r.step}`} hover>
                  <TableCell>
                    <Tooltip title={`Run ${r.tier} step ${r.step}`}>
                      <IconButton
                        size="small"
                        aria-label={`run ${r.tier} step ${r.step}`}
                        onClick={() =>
                          setRunTarget({
                            kind: "step",
                            tier: r.tier,
                            file: r.file,
                            step: r.step,
                            script: r.service,
                            args: r.args,
                          })
                        }
                      >
                        <PlayArrow fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{r.tier}</TableCell>
                  <TableCell align="right" sx={{ fontFamily: "monospace" }}>
                    {r.step}
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{r.service}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                    {r.args.length > 0 ? r.args.join(" ") : "—"}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      variant="outlined"
                      color={r.canBeParallel ? "warning" : "default"}
                      label={r.canBeParallel ? "parallel" : "serial"}
                    />
                  </TableCell>
                  <TableCell sx={{ maxWidth: 320 }}>
                    <Tooltip title={r.note ?? ""}>
                      <Typography variant="body2" noWrap sx={{ maxWidth: 320 }}>
                        {r.note ?? "—"}
                      </Typography>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
              {tiers != null && visible.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} align="center">
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ my: 2, display: "inline-block" }}
                    >
                      No orchestration step matches “{query}”
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </>
        )}
      </ConfigServiceTable>

      <RunOrchestrationDialog
        target={runTarget}
        onClose={() => setRunTarget(null)}
        onStarted={onStarted}
      />

      <Snackbar
        open={toast != null}
        autoHideDuration={6000}
        onClose={() => setToast(null)}
        message={toast}
      />
    </Box>
  );
}
