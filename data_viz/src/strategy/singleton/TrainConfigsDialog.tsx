/**
 * TrainConfigsDialog — "Model Configs (DB)" view of the Train Model
 * split-button dropdown.
 *
 * Reads strategy.algo_configs via GET /api/strategy/singleton/train-info
 * and shows, side by side:
 *   - DEFAULT row  (is_default = TRUE): the algo's DEFAULT_PARAMS on the
 *     reserved wide range [1900-01-01, 9999-12-31] — never overwritten.
 *   - TRAINED rows (is_default = FALSE): one row per train date
 *     [train_date, 9999-12-31]; the LATEST is the active config the
 *     loader picks (ORDER BY start_date DESC).
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import {
  fetchTrainInfo,
  type TrainConfigRow,
  type TrainInfoResponse,
  type TrainRunRow,
} from "@/lib/api-client";
import type { MaSpreadSecType } from "@shared/types";

/** Shape of the 2026-11-19 train-gate block inside the run's report
 *  bundle (training_runs.full_series_metrics.gate — written by
 *  training_store when the run carried a gate comparison). */
interface GateBlock {
  passed?: boolean;
  winner?: { metrics?: Record<string, unknown> };
  default?: { metrics?: Record<string, unknown> };
}

/** Shape of the CSCV-lite PBO diagnostic block (report.pbo). */
interface PboBlock {
  pbo?: number;
  n_pairs?: number;
  spearman_rho?: number;
}

function fmtNum(v: unknown, digits = 3): string {
  return typeof v === "number" ? v.toFixed(digits) : "n/a";
}

/** The latest run's gate verdict + PBO line (plain text — the train-gate
 *  ship decision is the headline: was the trained config shipped, or did
 *  the untrained default win on the untouched tail?). */
function LatestRunGate({ run }: { run: TrainRunRow | undefined }) {
  if (!run || run.status !== "completed") return null;
  const report = run.report;
  const gate: GateBlock | undefined = report?.gate as GateBlock | undefined;
  const pbo: PboBlock | undefined = report?.pbo as PboBlock | undefined;
  if (!gate && !pbo) return null;
  const w = gate?.winner?.metrics ?? {};
  const d = gate?.default?.metrics ?? {};
  return (
    <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1.5, px: 1.5, py: 1, mb: 1.5 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
        <Typography variant="caption" color="text.secondary">
          run #{run.run_id}
        </Typography>
        {gate && (
          <Chip
            size="small"
            color={gate.passed ? "success" : "warning"}
            label={gate.passed
              ? "GATE PASSED · trained config shipped"
              : "GATE RETAINED DEFAULT"}
          />
        )}
        {pbo && typeof pbo.pbo === "number" && (
          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
            PBO ≈ {pbo.pbo.toFixed(2)} ({pbo.n_pairs ?? "?"} pairs,
            ρ {fmtNum(pbo.spearman_rho, 2)}) — coarse overfit diagnostic
          </Typography>
        )}
      </Box>
      {gate && (
        <Typography variant="caption" sx={{ display: "block", mt: 0.5, fontFamily: "monospace" }}>
          gate segment: trained calmar {fmtNum(w.calmar)} / risk {fmtNum(w.risk_score)}
          {"  vs  "}default calmar {fmtNum(d.calmar)} / risk {fmtNum(d.risk_score)}
        </Typography>
      )}
    </Box>
  );
}

export interface TrainConfigsDialogProps {
  open: boolean;
  onClose: () => void;
  code: string;
  secType: MaSpreadSecType;
  strategyName: string;
}

function fmtVal(v: unknown): string {
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(6);
  }
  if (v !== null && typeof v === "object") {
    // nested param payloads (e.g. the comb config block) — render as
    // JSON instead of "[object Object]"
    return JSON.stringify(v);
  }
  return String(v);
}

function ParamsTable({ params }: { params: Record<string, unknown> }) {
  const keys = Object.keys(params).sort();
  if (keys.length === 0) {
    return <Typography variant="body2" color="text.secondary">(empty)</Typography>;
  }
  return (
    <Table size="small" sx={{ "& th, & td": { px: 1, py: 0.5 } }}>
      <TableHead>
        <TableRow>
          <TableCell width="45%">Param</TableCell>
          <TableCell>Value</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {keys.map((k) => (
          <TableRow key={k} hover>
            <TableCell sx={{ fontFamily: "monospace" }}>{k}</TableCell>
            <TableCell sx={{ fontFamily: "monospace" }}>{fmtVal(params[k])}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function ConfigCard({
  label,
  chip,
  chipColor,
  config,
  defaultExpanded,
}: {
  label: string;
  chip?: string;
  chipColor?: "default" | "primary" | "success" | "warning";
  config: TrainConfigRow;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(Boolean(defaultExpanded));
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1.5,
        overflow: "hidden",
        mb: 1.5,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          py: 1,
          cursor: "pointer",
          bgcolor: "action.hover",
          userSelect: "none",
        }}
        onClick={() => setExpanded((e) => !e)}
      >
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          {label}
        </Typography>
        {chip && <Chip size="small" color={chipColor} label={chip} />}
        <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
          {config.start_date ?? "?"} → {config.end_date ?? "?"}
          {config.updated_at ? ` · upd ${config.updated_at.slice(0, 16).replace("T", " ")}` : ""}
        </Typography>
        <Typography variant="body2">{expanded ? "▾" : "▸"}</Typography>
      </Box>
      {expanded && (
        <Box sx={{ px: 1.5, py: 1, maxHeight: 260, overflow: "auto" }}>
          <ParamsTable params={config.params} />
        </Box>
      )}
    </Box>
  );
}

export function TrainConfigsDialog(props: TrainConfigsDialogProps) {
  const { open, onClose, code, secType, strategyName } = props;
  const [data, setData] = useState<TrainInfoResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch on open (+ manual refresh). Plain-fetch client — always fresh.
  // `strategyName` is the FT-LESS config key the trainer writes under
  // (family name / "portfolio:…") — passed through verbatim so the
  // dialog shows the rows for the ACTUAL selection.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchTrainInfo(code, secType, strategyName)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, code, secType, strategyName]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, pb: 1 }}>
        Model Configs · {secType} {code} · {strategyName}
        <Tooltip title="Refresh from DB">
          <IconButton
            size="small"
            onClick={() => {
              setLoading(true);
              setError(null);
              fetchTrainInfo(code, secType, strategyName)
                .then(setData)
                .catch((e) => setError(String(e instanceof Error ? e.message : e)))
                .finally(() => setLoading(false));
            }}
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </DialogTitle>
      <DialogContent dividers>
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        )}
        {error && <Alert severity="error">{error}</Alert>}
        {!loading && !error && data && (
          <>
            <LatestRunGate run={data.runs[0]} />
            {data.default ? (
              <ConfigCard
                label="Default (algo DEFAULT_PARAMS)"
                chip="DEFAULT"
                chipColor="default"
                config={data.default}
                defaultExpanded={!data.trained.length}
              />
            ) : (
              <Alert severity="info" sx={{ mb: 1.5 }}>
                No default row yet — it is created by the first Run Strategy
                (ensure_default_config).
              </Alert>
            )}
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Trained configs ({data.trained.length}) — latest is active
            </Typography>
            {data.trained.length === 0 && (
              <Alert severity="info">
                No trained rows yet — click Train Model to run a study.
              </Alert>
            )}
            {data.trained.map((t, i) => (
              <ConfigCard
                key={`${t.start_date}-${i}`}
                label={`Trained ${t.start_date ?? ""}`}
                chip={i === 0 ? "ACTIVE" : undefined}
                chipColor="primary"
                config={t}
                defaultExpanded={i === 0 && !data.default}
              />
            ))}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
