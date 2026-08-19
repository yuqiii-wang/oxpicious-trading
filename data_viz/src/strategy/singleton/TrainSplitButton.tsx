/**
 * TrainSplitButton — the Train Model action as a SPLIT button:
 *
 *   [ ▶ Train Model | ▼ ]
 *
 *   - Main button: runs the nested trainer (unchanged behaviour).
 *   - Side down-triangle button: opens a menu with
 *       "Model Configs (DB)"   → TrainConfigsDialog (default + trained
 *                                 algo_configs rows, is_default split).
 *       "Training Logs"        → TrainLogsDialog (runs + the two loss
 *                                 types — Set A Omega / Set B Calmar —
 *                                 displayed separately).
 */
import { useRef, useState } from "react";
import {
  Button,
  ButtonGroup,
  CircularProgress,
  ListItemIcon,
  MenuItem,
  Menu,
} from "@mui/material";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import ModelTrainingIcon from "@mui/icons-material/ModelTraining";
import TuneIcon from "@mui/icons-material/Tune";
import ListAltIcon from "@mui/icons-material/ListAlt";
import { TrainConfigsDialog } from "./TrainConfigsDialog";
import { TrainLogsDialog } from "./TrainLogsDialog";
import type { MaSpreadSecType } from "@shared/types";

export interface TrainSplitButtonProps {
  training: boolean;
  disabled: boolean;
  onTrainClick: () => void;
  /** Context the dropdown dialogs query the DB with. */
  code: string;
  secType: MaSpreadSecType;
  strategyName: string;
}

export function TrainSplitButton(props: TrainSplitButtonProps) {
  const { training, disabled, onTrainClick, code, secType, strategyName } = props;
  const anchorRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [configsOpen, setConfigsOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);

  return (
    <>
      <ButtonGroup
        variant="contained"
        size="small"
        color="secondary"
        ref={anchorRef}
      >
        <Button
          disabled={disabled}
          startIcon={
            training ? <CircularProgress size={16} color="inherit" /> : <ModelTrainingIcon />
          }
          onClick={onTrainClick}
          title="Nested trainer (TPE → top-K → Kelly → grid) over the algo's tunable space; best params are upserted to algo_configs"
        >
          {training ? "Training…" : "Train Model"}
        </Button>
        <Button
          size="small"
          aria-label="Train Model options"
          title="Model configs (DB) & training logs"
          onClick={() => setMenuOpen(true)}
        >
          <ArrowDropDownIcon />
        </Button>
      </ButtonGroup>

      <Menu
        anchorEl={anchorRef.current}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        <MenuItem
          onClick={() => {
            setMenuOpen(false);
            setConfigsOpen(true);
          }}
        >
          <ListItemIcon>
            <TuneIcon fontSize="small" />
          </ListItemIcon>
          Model Configs (DB)…
        </MenuItem>
        <MenuItem
          onClick={() => {
            setMenuOpen(false);
            setLogsOpen(true);
          }}
        >
          <ListItemIcon>
            <ListAltIcon fontSize="small" />
          </ListItemIcon>
          Training Logs…
        </MenuItem>
      </Menu>

      <TrainConfigsDialog
        open={configsOpen}
        onClose={() => setConfigsOpen(false)}
        code={code}
        secType={secType}
        strategyName={strategyName}
      />
      <TrainLogsDialog
        open={logsOpen}
        onClose={() => setLogsOpen(false)}
        code={code}
        secType={secType}
        strategyName={strategyName}
      />
    </>
  );
}
