/**
 * GET /fleet — the fleet fold: every area's resident rows WITH their
 * unified detail payload (heartbeat evidence + derived missing-set
 * progress + child-reported progress + log tail), via the monitor
 * area's aggregated GET /residents.
 * Full route docs: see index.ts.
 *
 * The 2026-11-21 control unification plan (Phase 3) query rule: the
 * Monitor UI queries the fleet VIA THE SERVER — ONE call into the
 * monitor server, whose FleetOps fans out ONE GET per area server
 * (``/processes?detail=1``) server-side. No per-process call is made
 * from the API or the browser; a per-service drill-down (a non-resident
 * pipeline run) rides the owning server's own
 * ``GET /processes/{service}/detail`` through its own route, never a
 * client-side fan-out. An area server predating the fold answers rows
 * without a ``detail`` bag — the shape degrades gracefully.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import {
  monitorResidents,
  type MonitorFleet,
} from "../../services/infra/monitor-client.service.js";
import { sendServiceCommand } from "../../services/infra/py-runner.service.js";

const router = Router();

/** GET /api/monitor/fleet — the whole fleet with per-resident detail
 *  folded (the Service Monitor page merges it into its resident rows by
 *  service name; the derived progress — poller per-asset completeness —
 *  exists ONLY here, computed from the data tables at the owning
 *  servers). A monitor failure surfaces as a 502 (the page's own
 *  /stream data keeps the table alive independently). */
router.get("/fleet", wrapAsync(async (_req: Request, res: Response) => {
  const fleet: MonitorFleet = await monitorResidents();
  res.json({ success: true, generatedAt: new Date().toISOString(), fleet });
}));

/** The downward command labels (static.label_dim domain service_command
 *  — 0 flush_now | 1 pause | 2 resume; the label strings are the wire
 *  contract the area server's route validates against its own mirror). */
const COMMAND_LABELS = new Set(["flush_now", "pause", "resume"]);

/** POST /api/monitor/fleet/command { service, command, payload? } — the
 *  Monitor UI's ONE downward-control face (the 2026-11-21 control
 *  unification plan Phase 4): a cooperative signal (flush_now / pause /
 *  resume) enqueued into the target child's mailbox at its OWNING area
 *  server (the py-runner's cluster routing — the same prefix table the
 *  runs use). The child's heartbeat thread claims + consumes the
 *  command atomically within ~5s and acts at its next safe checkpoint;
 *  the area server's class gate (service_config resource_class /
 *  pausable) refuses a PAUSE for a not-pausable or gpu-class service
 *  with the reason — surfaced verbatim as a 409. */
router.post("/fleet/command", wrapAsync(async (req: Request, res: Response) => {
  const body = (req.body ?? {}) as {
    service?: unknown;
    command?: unknown;
    payload?: unknown;
  };
  const service = typeof body.service === "string" ? body.service : "";
  const command = typeof body.command === "string" ? body.command : "";
  if (!service || /[^A-Za-z0-9_.-]/.test(service)) {
    throw new HttpError(422, "INVALID_PARAM", `service ${JSON.stringify(service)} is not a plausible dotted service name`);
  }
  if (!COMMAND_LABELS.has(command)) {
    throw new HttpError(
      422,
      "INVALID_PARAM",
      `command ${JSON.stringify(command)} is not one of ${[...COMMAND_LABELS].sort().join(", ")}`,
    );
  }
  const payload = (body.payload != null && typeof body.payload === "object"
    && !Array.isArray(body.payload))
    ? (body.payload as Record<string, unknown>)
    : undefined;
  const verdict = await sendServiceCommand(service, command, payload);
  if (!verdict.enqueued) {
    throw new HttpError(409, "CONFLICT", verdict.refused);
  }
  res.json({
    success: true,
    service,
    command,
    enqueued: true,
    message: `${command} enqueued for ${service} — the child claims it `
      + "within ~5s and acts at its next safe checkpoint",
  });
}));

export default router;
