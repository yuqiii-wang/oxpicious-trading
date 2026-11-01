/**
 * RunHeartbeat — the Thread Graph view's channel-health chip for a
 * RUNNING run (rendered beside the "live" chip; nothing when settled).
 *
 * When the graph opens on a live run it sends ONE end-to-end heartbeat
 * probe — {"type":"ping","probe_id"} on the /api/ai/events channel,
 * relayed by the bridge to the deep-agents daemon, whose probe_id-
 * echoing pong broadcasts back — and re-probes every 30s while the run
 * stays active, so the monitoring chip shows the channel's CURRENT
 * health, not just the open instant's. Good → a green
 * `heartbeat {latency}ms` chip; a missed pong → a red `no pong` chip
 * (the 3s poll fallback keeps the graph updating even then, so this is
 * a channel alarm, not a data-stale alarm). The probe id matching is
 * per-instance: several open graphs each track their own probes.
 */
import { useEffect, useState } from "react";
import { Chip, Tooltip } from "@mui/material";
import { MonitorHeart } from "@mui/icons-material";
import {
  probeAiEvents,
  type AiHeartbeatProbe,
} from "@/lib/api-client/aiEvents";

const REPROBE_MS = 30_000;
const PROBE_TIMEOUT_MS = 5_000;

export default function RunHeartbeat({ active }: { active: boolean }) {
  const [probe, setProbe] = useState<AiHeartbeatProbe | null>(null);

  useEffect(() => {
    if (!active) {
      setProbe(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const run = async (): Promise<void> => {
      const result = await probeAiEvents(PROBE_TIMEOUT_MS);
      if (cancelled) return;
      setProbe(result);
      timer = setTimeout(() => void run(), REPROBE_MS);
    };
    void run();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [active]);

  if (!active) return null;
  if (probe === null) {
    return (
      <Chip
        size="small"
        variant="outlined"
        icon={<MonitorHeart />}
        label="checking…"
        sx={{ height: 22 }}
      />
    );
  }
  return (
    <Tooltip
      title={
        probe.ok
          ? `Heartbeat ok — browser → bridge → deep-agents daemon ponged in ` +
            `${probe.latencyMs}ms (at ${probe.at.toLocaleTimeString()}); ` +
            `re-probes every 30s while the run is live`
          : `The daemon did not answer the heartbeat probe ` +
            `(at ${probe.at.toLocaleTimeString()}) — the channel is down; ` +
            `the graph still updates through the 3s poll fallback`
      }
    >
      <Chip
        size="small"
        color={probe.ok ? "success" : "error"}
        variant="outlined"
        icon={<MonitorHeart />}
        label={probe.ok ? `heartbeat ${probe.latencyMs}ms` : "no pong"}
        sx={{ height: 22 }}
      />
    </Tooltip>
  );
}
