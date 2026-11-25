/**
 * RunHistorySelect — the run-history selector for the singleton page.
 *
 * A compact Select listing the recent strategy_identity runs (newest
 * first, from GET /singleton/run-history). The top entry is the
 * latest/active run; older entries show their period + stored config
 * signature (comb mode, params_version, trading keys) so runs with
 * different configs are distinguishable. An info button opens the full
 * params JSON of the DISPLAYED run.
 */
import { useState } from "react";
import {
  Box,
  Chip,
  IconButton,
  ListItemText,
  MenuItem,
  Popover,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { fmtDate } from "@/shared/datetime";
import type { RunHistoryItem } from "@shared/types";

/** Compact config signature from a run's stored params (different
 *  configs → different labels). The config_source block (stamped by the
 *  Python run) links the run to the algo_configs row it loaded —
 *  "trained 2026-11-15" vs "default". */
function configLabel(params: Record<string, unknown>): string {
  const parts: string[] = [];
  const source = params.config_source as {
    is_default?: boolean;
    start_date?: string;
    params_version?: number;
  } | undefined;
  if (source && typeof source === "object") {
    parts.push(source.is_default
      ? "src default"
      : `src trained ${fmtDate(source.start_date ?? "?")}`);
  }
  const comb = params.comb as Record<string, unknown> | undefined;
  if (comb && typeof comb === "object") {
    const enabled = (comb as { enabled?: boolean }).enabled;
    const weights = (comb as { weights?: { mode?: string } }).weights;
    parts.push(enabled === false ? "comb off" : `comb:${weights?.mode ?? "on"}`);
  }
  if (params.params_version != null) parts.push(`v${String(params.params_version)}`);
  if (params.min_holding_period != null) {
    parts.push(`mhp ${String(params.min_holding_period)}`);
  }
  if (params.conf_threshold != null) {
    parts.push(`thr ${Number(params.conf_threshold).toFixed(0)}`);
  }
  return parts.join(" · ");
}

/** fmt for param values — nested payloads render as JSON (the
 *  TrainConfigsDialog fmtVal pattern). */
function fmtVal(v: unknown): string {
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(6);
  }
  if (v !== null && typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export interface RunHistorySelectProps {
  runs: RunHistoryItem[];
  /** The displayed run's seq_id (null = latest/active). */
  selectedSeqId: number | null;
  onSelect: (seqId: number | null) => void;
  disabled?: boolean;
}

export function RunHistorySelect(
  { runs, selectedSeqId, onSelect, disabled }: RunHistorySelectProps,
) {
  const [infoOpen, setInfoOpen] = useState(false);
  const [infoAnchor, setInfoAnchor] = useState<HTMLElement | null>(null);

  const selected = runs.find((r) => r.seq_id === selectedSeqId) ?? null;
  const latest = runs.find((r) => r.is_active) ?? runs[0] ?? null;
  const viewingHistorical = selectedSeqId != null
    && latest != null && selectedSeqId !== latest.seq_id;

  if (runs.length === 0) return null;

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
      <TextField
        select
        size="small"
        label="Run"
        sx={{ minWidth: 250, maxWidth: 320 }}
        disabled={disabled}
        value={selectedSeqId ?? ""}
        onChange={(e) => {
          const v = String(e.target.value);
          onSelect(v === "" ? null : Number(v));
        }}
      >
        <MenuItem value="">
          <ListItemText
            primary={latest
              ? `Latest — #${latest.seq_no} · ${latest.start_date ? fmtDate(latest.start_date) : "?"} → ${latest.end_date ? fmtDate(latest.end_date) : "∞"}`
              : "Latest run"}
            secondary={latest ? configLabel(latest.params) || "current run" : null}
          />
        </MenuItem>
        {runs.map((r) => (
          <MenuItem key={r.seq_id} value={r.seq_id}>
            <ListItemText
              primary={
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <span>{`#${r.seq_no} · ${r.start_date ? fmtDate(r.start_date) : "?"} → ${r.end_date ? fmtDate(r.end_date) : "∞"}`}</span>
                  {r.is_active && <Chip size="small" color="primary" label="ACTIVE" />}
                  {r.status && r.status !== "completed"
                    && <Chip size="small" color="warning" label={r.status} />}
                </Box>
              }
              secondary={configLabel(r.params) || r.scenario || undefined}
            />
          </MenuItem>
        ))}
      </TextField>
      <Tooltip title={viewingHistorical
        ? "Params of this historical run (select Latest to return)"
        : "Params of the displayed run"}>
        <span>
          <IconButton
            size="small"
            disabled={disabled || (!selected && !latest)}
            onClick={(e) => { setInfoAnchor(e.currentTarget); setInfoOpen(true); }}
          >
            <InfoOutlinedIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Popover
        open={infoOpen}
        anchorEl={infoAnchor}
        onClose={() => { setInfoOpen(false); setInfoAnchor(null); }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        <Box sx={{ p: 1.5, maxHeight: 420, maxWidth: 460, overflow: "auto" }}>
          <Typography variant="caption" color="text.secondary">
            {selectedSeqId != null && selected
              ? `Run #${selected.seq_no} params (seq_id ${selected.seq_id})`
              : latest
                ? `Latest run #${latest.seq_no} params (seq_id ${latest.seq_id})`
                : "Params"}
          </Typography>
          {Object.entries(selected?.params ?? latest?.params ?? {})
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([k, v]) => (
              <Box key={k} sx={{ display: "flex", gap: 1, py: 0.25 }}>
                <Typography
                  component="div"
                  sx={{ fontFamily: "monospace", fontSize: 12, minWidth: 130 }}
                >
                  {k}
                </Typography>
                <Typography
                  component="div"
                  sx={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}
                >
                  {fmtVal(v)}
                </Typography>
              </Box>
            ))}
        </Box>
      </Popover>
    </Box>
  );
}
