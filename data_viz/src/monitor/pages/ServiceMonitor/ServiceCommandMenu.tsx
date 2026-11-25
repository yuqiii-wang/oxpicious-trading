/**
 * ServiceCommandMenu — ONE service's downward-control menu (the
 * 2026-11-21 control unification plan's Phase 4 mailbox face): Flush
 * now / Pause / Resume, enqueued into the target child's mailbox at its
 * OWNING area server (UI → Express /api/monitor/fleet/command → the
 * area's POST /processes/{service}/command → the child's heartbeat
 * thread claims + consumes it atomically within ~5s and acts at its
 * next safe checkpoint; SIGTERM stays the backstop).
 *
 * Gating: the menu renders only when the service is ALIVE (a down or
 * stopped service has no live child to signal — the row passes its own
 * liveness). A PAUSE is disabled when the fleet fold's config says the
 * service is not pausable or gpu-class (a paused GPU step pins the CUDA
 * context until exit — kill-and-restart stays its only give-way); for
 * rows without fold config (batch processes) the area server's class
 * gate refuses with the reason and the page surfaces it verbatim. The
 * button spins while this service's command is in flight.
 */
import { useState } from "react";
import {
  CircularProgress,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Tooltip,
} from "@mui/material";
import {
  DoneAll,
  MoreVert,
  PauseCircleOutlined,
  PlayCircleOutlined,
} from "@mui/icons-material";
import type { ServiceCommandLabel } from "@/lib/api-client/monitor";

export interface ServiceCommandGate {
  pausable?: boolean;
  resource_class?: string;
}

export default function ServiceCommandMenu({
  service,
  alive,
  gate,
  busy,
  onCommand,
}: {
  service: string;
  /** Liveness of the target (a dead service has no child to signal). */
  alive: boolean;
  /** The fleet fold's config summary when known (resident rows) — the
   *  pause gate's inputs; null for batch rows (the server gates). */
  gate: ServiceCommandGate | null;
  /** True while this service's command POST is in flight. */
  busy: boolean;
  /** Fire one command label (the page owns the POST + verdict alert). */
  onCommand: (command: ServiceCommandLabel) => void;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const pauseRefused =
    gate != null &&
    (gate.pausable === false ||
      gate.pausable === undefined ||
      gate.resource_class === "gpu");

  const pauseReason =
    gate == null
      ? "the owning server's class gate decides (a not-pausable or gpu-class service is refused with the reason)"
      : pauseRefused
        ? `${service} does not accept PAUSE (pausable=false or resource_class=${gate.resource_class ?? "gpu"} — a paused gpu step pins the CUDA context until exit; kill-and-restart stays its only give-way)`
        : `holds at the child's next safe checkpoint, auto-resumes after ${gate.resource_class === "host" ? "600s" : "1800s"} unless resumed sooner`;
  const fire = (command: ServiceCommandLabel) => {
    setAnchor(null);
    onCommand(command);
  };
  return (
    <>
      <Tooltip
        title={
          alive
            ? "send a downward signal — flush / pause / resume (the Phase 4 command mailbox; the child claims it within ~5s)"
            : "no live child to signal"
        }
        arrow
      >
        <span>
          <IconButton
            size="small"
            aria-label={`send command to ${service}`}
            onClick={(e) => setAnchor(e.currentTarget)}
            disabled={!alive || busy}
            sx={{ border: 1, borderColor: "divider" }}
          >
            {busy ? (
              <CircularProgress size={13} thickness={5} />
            ) : (
              <MoreVert fontSize="small" />
            )}
          </IconButton>
        </span>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={anchor != null}
        onClose={() => setAnchor(null)}
      >
        <MenuItem
          onClick={() => fire("flush_now")}
          disableRipple
        >
          <ListItemIcon>
            <DoneAll fontSize="small" />
          </ListItemIcon>
          Flush now
        </MenuItem>
        <Tooltip title={pauseReason} placement="right" arrow>
          <span>
            <MenuItem
              onClick={() => fire("pause")}
              disabled={pauseRefused}
              disableRipple
            >
              <ListItemIcon>
                <PauseCircleOutlined fontSize="small" />
              </ListItemIcon>
              Pause
            </MenuItem>
          </span>
        </Tooltip>
        <MenuItem onClick={() => fire("resume")} disableRipple>
          <ListItemIcon>
            <PlayCircleOutlined fontSize="small" />
          </ListItemIcon>
          Resume
        </MenuItem>
      </Menu>
    </>
  );
}
