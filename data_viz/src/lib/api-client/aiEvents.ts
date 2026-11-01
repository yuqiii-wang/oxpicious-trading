/**
 * AI deep-agent event channel — the browser's WebSocket client for
 * daemon state updates. The deep-agents daemon publishes thread/state
 * lifecycle events (thread_started when a run registers, a
 * state_created/state_updated per planning-store write, node_finished
 * on every node-lane settle, thread_settled at the end); the data_viz
 * API bridges them at /api/ai/events (api/services/ai-events.service.ts).
 *
 * ONE module-level connection shared by every subscriber (components
 * mount/unmount handlers, not sockets): auto-reconnect with capped
 * backoff, and a synthetic "hello" delivered to listeners on every
 * (re)connect so a page can re-sync anything it may have missed while
 * the channel was down. Events are notifications — the payloads carry
 * identity fields (thread_id, state_id, status), and the consumers
 * re-read the run/thread endpoints they already have. While the channel
 * is down the pages' poll loops are the fallback; events only make the
 * updates instant.
 */

/** The daemon event vocabulary (llm_agents/deep_agents/events.py + the
 *  server/bridge hellos) plus the bridge's own injected frame: the run
 *  delete broadcasts "thread_deleted" bridge-side (the daemon stream
 *  carries run lifecycle only) so every open page refreshes. */
export type AiDaemonEventType =
  | "hello"
  | "thread_started"
  | "state_created"
  | "state_updated"
  /** The node LANE's own settle (NodeRunWriter.finish — every node
   *  finish announces itself; covers the nodes whose state-row events
   *  don't exist: the degraded execute node, a payload-less result). */
  | "node_finished"
  | "states_settled"
  | "result_recorded"
  | "thread_settled"
  /** Daemon liveness frame (server.py WS_HEARTBEAT_SECONDS — sent when
   *  no event flowed for 20s): proves the channel end to end; the
   *  consumers ignore it, the page's poll fallback never needed it. */
  | "heartbeat"
  /** The daemon's reply to the bridge's active "ping" probe (not
   *  normally seen by pages). */
  | "pong"
  /** Bridge-injected (not a daemon frame): a run entry was deleted —
   *  thread_id names it; consumers re-read the strip and collapse an
   *  open graph of that thread. */
  | "thread_deleted";

/** One daemon event as the bridge forwards it (identity fields only —
 *  the shapes are open: unknown extras are ignored, a missing field is
 *  read through `typeof` guards at the consumer). */
export interface AiDaemonEvent {
  type: AiDaemonEventType;
  /** The run the event concerns (absent on hello). */
  thread_id?: string;
  state_id?: number;
  node?: string;
  title?: string;
  status?: string;
  ordinal?: number;
  parent_state_id?: number | null;
  /** thread_settled: how the run ended. */
  outcome?: "completed" | "stopped" | "failed";
  /** hello: the in-flight thread ids (the daemon's frame; the bridge's
   *  hello carries `daemon: boolean` instead). */
  active?: string[];
  count?: number;
  ts?: string;
  /** pong: the echoed probe id (the daemon relays it; the bridge's
   *  local failure pong carries it too) — how a probe matches its own
   *  round-trip when pongs broadcast to every subscriber. */
  probe_id?: string;
  /** pong: false only on the bridge's LOCAL answer (upstream down);
   *  the daemon's pong implies end-to-end. */
  ok?: boolean;
  daemon?: boolean;
}

type Listener = (event: AiDaemonEvent) => void;

const listeners = new Set<Listener>();
let socket: WebSocket | null = null;
let connecting = false;
let connectTimer: ReturnType<typeof setTimeout> | null = null;
let backoffMs = 500;

function isOpen(): boolean {
  return socket !== null && socket.readyState === WebSocket.OPEN;
}

function emit(event: AiDaemonEvent): void {
  for (const listener of listeners) listener(event);
}

function scheduleReconnect(): void {
  if (connectTimer !== null || listeners.size === 0) return;
  connectTimer = setTimeout(() => {
    connectTimer = null;
    connect();
  }, backoffMs);
  backoffMs = Math.min(backoffMs * 2, 15_000);
}

function connect(): void {
  if (connecting || socket !== null) return;
  connecting = true;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${window.location.host}/api/ai/events`;
  let ws: WebSocket;
  try {
    ws = new WebSocket(url);
  } catch {
    connecting = false;
    scheduleReconnect();
    return;
  }
  socket = ws;
  ws.onopen = () => {
    connecting = false;
    backoffMs = 500;
    // Synthetic resync marker (the bridge's own hello frame follows on
    // the wire): lets a page re-read anything missed while offline.
    emit({ type: "hello" });
  };
  ws.onmessage = (msg: MessageEvent) => {
    let event: AiDaemonEvent | null = null;
    try {
      event = JSON.parse(String(msg.data)) as AiDaemonEvent;
    } catch {
      return; // a non-JSON frame is not worth a channel reset
    }
    if (event !== null && typeof event.type === "string") emit(event);
  };
  ws.onclose = () => {
    socket = null;
    connecting = false;
    scheduleReconnect();
  };
  ws.onerror = () => {
    // onclose follows every failed open — the reconnect lives there.
  };
}

/** Subscribe to deep-agent events. Connects lazily on the first
 *  listener; the connection then stays open for the page's life (later
 *  subscribers join the existing socket). Returns the unsubscribe. */
export function subscribeAiEvents(listener: Listener): () => void {
  listeners.add(listener);
  if (!isOpen() && socket === null && connectTimer === null) connect();
  return () => {
    listeners.delete(listener);
  };
}

/** One heartbeat probe's result: ok = a pong matched this probe's id
 *  within the timeout (end-to-end browser → bridge → daemon, the run
 *  the Thread Graph view is monitoring lives on the daemon); latencyMs
 *  is null when the probe failed before any pong (channel down or
 *  timeout). */
export interface AiHeartbeatProbe {
  ok: boolean;
  latencyMs: number | null;
  at: Date;
}

/** One end-to-end channel heartbeat: send
 *  {"type":"ping","probe_id"} on the shared socket and resolve on the
 *  matching pong — the daemon echoes the probe_id, the bridge answers
 *  locally with ok:false when the upstream is down. Resolves
 *  {ok:false} at once when the socket is not open. (The probe does NOT
 *  register a listener — probes are transient, matched by id only.) */
export function probeAiEvents(timeoutMs = 5_000): Promise<AiHeartbeatProbe> {
  return new Promise((resolve) => {
    const settled = { done: false };
    const finish = (ok: boolean, latencyMs: number | null): void => {
      if (settled.done) return;
      settled.done = true;
      resolve({ ok, latencyMs, at: new Date() });
    };
    const ws = socket;
    if (ws === null || ws.readyState !== WebSocket.OPEN || connecting) {
      finish(false, null);
      return;
    }
    const probeId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `probe-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const t0 = performance.now();
    const timer = setTimeout(() => {
      listeners.delete(listener);
      finish(false, null);
    }, timeoutMs);
    const listener = (ev: AiDaemonEvent): void => {
      if (ev.type !== "pong" || ev.probe_id !== probeId) return;
      clearTimeout(timer);
      listeners.delete(listener);
      finish(ev.ok !== false, Math.round(performance.now() - t0));
    };
    listeners.add(listener);
    ws.send(JSON.stringify({ type: "ping", probe_id: probeId }));
  });
}
