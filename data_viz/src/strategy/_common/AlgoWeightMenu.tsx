/**
 * AlgoWeightMenu — per-algo weight selector for Singleton Strategy.
 *
 * Replaces the single-algo dropdown with a popover where each algo has a
 * weight slider (0-100%). The resolved strategy_name is computed from the
 * selection:
 *   - Binary (one algo non-zero): strategy_name = algo name (e.g. "macd")
 *   - Mixed (multiple non-zero): strategy_name = "portfolio:macd*0.5"
 *
 * The sum indicator turns red when weights don't sum to 100%. A "Normalize"
 * button auto-scales existing non-zero weights to sum to 100%.
 *
 * Default selection: { macd: 1.0 }.
 */
import { useState, type MouseEvent } from "react";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  Popover,
  Stack,
  Slider,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import TuneIcon from "@mui/icons-material/Tune";
import {
  ALGO_LABELS,
  STRATEGY_ALGOS,
  selectionLabel,
  selectionSum,
  type StrategySelection,
} from "@/lib/api-client";

interface AlgoWeightMenuProps {
  selection: StrategySelection;
  onChange: (selection: StrategySelection) => void;
  disabled?: boolean;
  /** Fault tolerance percentage (0-20). 0 = disabled. */
  faultTolerance: number;
  /** Callback when fault tolerance changes. */
  onFaultToleranceChange: (ft: number) => void;
}

const EPSILON = 1e-6;

export default function AlgoWeightMenu({
  selection,
  onChange,
  disabled,
  faultTolerance,
  onFaultToleranceChange,
}: AlgoWeightMenuProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);

  const open = Boolean(anchorEl);
  const sum = selectionSum(selection);
  const valid = Math.abs(sum - 1.0) < 1e-3;
  const ftEnabled = faultTolerance > 0;
  const label = selectionLabel(selection, faultTolerance);

  const handleOpen = (e: MouseEvent<HTMLElement>) => {
    if (disabled) return;
    setAnchorEl(e.currentTarget);
  };

  const handleClose = () => setAnchorEl(null);

  // Update one algo's weight (0-1 float); leaves others unchanged.
  const updateWeight = (algo: (typeof STRATEGY_ALGOS)[number], weight: number) => {
    const clamped = Math.max(0, Math.min(1, weight));
    onChange({ ...selection, [algo]: clamped });
  };

  // Auto-scale non-zero weights so they sum to 1.0. If all are zero, sets
  // the default (macd=1.0).
  const normalize = () => {
    const total = selectionSum(selection);
    if (total < EPSILON) {
      onChange({ macd: 1.0 });
      return;
    }
    const next: StrategySelection = { ...selection };
    for (const a of STRATEGY_ALGOS) {
      next[a] = (selection[a] || 0) / total;
    }
    // Round to 2 decimals to avoid float drift (0.33333... -> 0.33).
    for (const a of STRATEGY_ALGOS) {
      next[a] = Math.round(next[a] * 100) / 100;
    }
    // Fix rounding residue on the largest weight.
    const residue = 1 - selectionSum(next);
    if (Math.abs(residue) > EPSILON) {
      const largest = STRATEGY_ALGOS.reduce((best, a) =>
        next[a] > next[best] ? a : best, STRATEGY_ALGOS[0]);
      next[largest] = Math.round((next[largest] + residue) * 100) / 100;
    }
    onChange(next);
  };

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={handleOpen}
        disabled={disabled}
        startIcon={<TuneIcon />}
        title="Algo weights — click to adjust the per-algo weight mix"
        sx={{
          minWidth: 160,
          height: 36,
          borderColor: valid ? "divider" : "error.main",
          color: valid ? "text.primary" : "error.main",
          fontWeight: 600,
          textTransform: "none",
        }}
      >
        {label}
      </Button>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 2, width: 340 } } }}
      >
        <Stack spacing={1.5}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Algo Weights
          </Typography>
          {STRATEGY_ALGOS.map((algo) => (
            <Box key={algo}>
              <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 0.5 }}>
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  {ALGO_LABELS[algo]}
                </Typography>
                <TextField
                  size="small"
                  value={Math.round(selection[algo] * 100)}
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 10);
                    if (Number.isFinite(v)) updateWeight(algo, v / 100);
                  }}
                  InputProps={{
                    inputProps: { min: 0, max: 100, step: 5, style: { width: 48, textAlign: "right", padding: "2px 4px" } },
                    endAdornment: <Typography variant="caption" sx={{ ml: 0.25 }}>%</Typography>,
                  }}
                  sx={{ width: 90 }}
                />
              </Box>
              <Slider
                size="small"
                min={0}
                max={100}
                step={5}
                value={Math.round(selection[algo] * 100)}
                onChange={(_, v) => updateWeight(algo, (v as number) / 100)}
              />
            </Box>
          ))}
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", pt: 1, borderTop: 1, borderColor: "divider" }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 600,
                color: valid ? "success.main" : "error.main",
              }}
            >
              Sum: {Math.round(sum * 100)}% {valid ? "" : "— must be 100%"}
            </Typography>
            <Tooltip title="Scale non-zero weights so they sum to 100%">
              <IconButton size="small" onClick={normalize} disabled={disabled}>
                <TuneIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>

          {/* Fault Tolerance — adverse OHLC stress test on decision days.
              When enabled, the strategy_name gets an _ft{N} suffix so the
              FT variant is a distinct strategy in the DB. The slider sets
              the stress percentage (0-20%); the checkbox toggles it on/off. */}
          <Box sx={{ pt: 1, borderTop: 1, borderColor: "divider" }}>
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={ftEnabled}
                  disabled={disabled}
                  onChange={(e) => onFaultToleranceChange(
                    e.target.checked ? (faultTolerance || 10) : 0,
                  )}
                />
              }
              label={
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  Fault Tolerance
                </Typography>
              }
            />
            <Tooltip
              title="Adversely perturbs OHLC on BUY/SELL decision days (BUY up, SELL down) by this % of |Δclose|. Indicators are recomputed and the algo re-runs on stressed data. Produces a separate _ft{N} strategy."
              placement="top"
            >
              <Box sx={{ px: 1, opacity: ftEnabled ? 1 : 0.5 }}>
                <Slider
                  size="small"
                  min={1}
                  max={20}
                  step={1}
                  value={faultTolerance > 0 ? faultTolerance : 10}
                  disabled={disabled || !ftEnabled}
                  onChange={(_, v) => onFaultToleranceChange(v as number)}
                  valueLabelDisplay="auto"
                  valueLabelFormat={(v) => `${v}%`}
                />
                <Typography variant="caption" color="text.secondary">
                  {ftEnabled
                    ? `Stress = ${Math.round(faultTolerance)}% of daily price change`
                    : "Tick to enable adverse OHLC stress test"}
                </Typography>
              </Box>
            </Tooltip>
          </Box>
        </Stack>
      </Popover>
    </>
  );
}