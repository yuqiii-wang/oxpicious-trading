/**
 * FtControl — the Fault Tolerance stress-test toggle for the Singleton
 * Strategy action bar (moved out of the retired AlgoWeightMenu).
 *
 * When enabled (1-20%), the Python runner does a two-pass stress test:
 * OHLC on decision days is adversely perturbed by ft% of |Δclose| and
 * the strategy re-runs on the stressed data; the strategy_name gets an
 * _ft{N} suffix so the FT variant is a distinct strategy in the DB.
 */
import { useState, type MouseEvent } from "react";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Popover,
  Slider,
  Tooltip,
  Typography,
} from "@mui/material";
import BoltIcon from "@mui/icons-material/Bolt";

interface FtControlProps {
  faultTolerance: number;
  onFaultToleranceChange: (ft: number) => void;
  disabled?: boolean;
}

export default function FtControl({
  faultTolerance,
  onFaultToleranceChange,
  disabled,
}: FtControlProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const open = Boolean(anchorEl);
  const ftEnabled = faultTolerance > 0;

  const handleOpen = (e: MouseEvent<HTMLElement>) => {
    if (disabled) return;
    setAnchorEl(e.currentTarget);
  };

  return (
    <>
      <Button
        size="small"
        variant={ftEnabled ? "contained" : "outlined"}
        color={ftEnabled ? "warning" : "inherit"}
        onClick={handleOpen}
        disabled={disabled}
        startIcon={<BoltIcon />}
        title="Fault tolerance — adverse OHLC stress test on decision days"
        sx={{ height: 36, fontWeight: 600, textTransform: "none" }}
      >
        {ftEnabled ? `FT ${Math.round(faultTolerance)}%` : "FT off"}
      </Button>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 2, width: 320 } } }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
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
            title="Adversely perturbs OHLC on BUY/SELL decision days (BUY up, SELL down) by this % of |Δclose|. The stressed re-run produces a separate _ft{N} strategy."
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
      </Popover>
    </>
  );
}
