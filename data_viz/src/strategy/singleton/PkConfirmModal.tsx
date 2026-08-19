/**
 * PkConfirmModal — confirmation dialog when a PK already exists.
 *
 * Shows details of the existing strategy run and asks the user whether
 * to force a re-run (which will delete the existing results and recompute).
 */
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from "@mui/material";
import type { CheckExistingResult } from "@/lib/api-client";
import type { MaSpreadSecType } from "@shared/types";

export interface PkConfirmModalProps {
  open: boolean;
  searchCode: string;
  secType: MaSpreadSecType;
  existingRun: CheckExistingResult | null;
  onClose: () => void;
  onForceRerun: () => void;
}

export function PkConfirmModal(props: PkConfirmModalProps) {
  const { open, searchCode, secType, existingRun, onClose, onForceRerun } = props;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="force-rerun-dialog-title"
    >
      <DialogTitle id="force-rerun-dialog-title">
        Existing strategy run detected
      </DialogTitle>
      <DialogContent>
        <DialogContentText>
          A backtest run already exists for{" "}
          <b>{searchCode}</b> ({secType}) with this strategy configuration.
        </DialogContentText>
        {existingRun && (
          <Box sx={{ mt: 1.5, fontSize: "0.85rem", color: "text.secondary" }}>
            <div>
              Seq #{existingRun.seq_no}
              {existingRun.status && (
                <Box
                  component="span"
                  sx={{
                    ml: 1,
                    px: 0.75,
                    py: 0.25,
                    borderRadius: 1,
                    bgcolor: existingRun.status === "completed"
                      ? "success.main"
                      : "warning.main",
                    color: "common.white",
                    fontSize: "0.75rem",
                  }}
                >
                  {existingRun.status}
                </Box>
              )}
            </div>
            <div>Period: {existingRun.start_date}{existingRun.end_date ? ` → ${existingRun.end_date}` : " → null"}</div>
            {existingRun.scenario && (
              <div>Scenario: {existingRun.scenario}</div>
            )}
            {(existingRun.fault_tolerance ?? 0) > 0 && (
              <div>FT: {existingRun.fault_tolerance}%</div>
            )}
          </Box>
        )}
        <DialogContentText sx={{ mt: 2 }}>
          Do you want to force a re-run? This will delete the existing
          results and compute fresh backtest + forecast data.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button
          onClick={onClose}
          color="inherit"
        >
          Cancel
        </Button>
        <Button
          onClick={onForceRerun}
          color="primary"
          variant="contained"
          autoFocus
        >
          Force Re-run
        </Button>
      </DialogActions>
    </Dialog>
  );
}
