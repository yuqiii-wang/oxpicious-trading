/**
 * AI deep-agent events — the WebSocket bridge between the deep-agents
 * daemon's event stream (llm_agents/deep_agents/server.py WS /ws — one
 * JSON frame per thread/state lifecycle event: thread_started,
 * state_created/updated, states_settled, result_recorded,
 * thread_settled) and the browser clients of /api/ai/events.
 *
 * UPSTREAM: ONE daemon connection for the whole API process, opened at
 * boot (startAiEventsBridge, server.ts) and reconnected with capped
 * backoff — the daemon restarts under the stream watchdog, and a run
 * survives the gap through the UI's poll fallbacks; the reconnect
 * handshake's hello re-syncs the in-flight thread ids. Liveness is
 * measured, not assumed: the daemon sends an app-level heartbeat frame
 * every 20s when idle (server.py WS_HEARTBEAT_SECONDS), and a checker
 * terminates the socket when no frame arrives for 45s — a half-open
 * connection (daemon killed hard, hung WSL forward) otherwise never
 * fires "close" and the bridge would wait forever. Frames are
 * forwarded VERBATIM (raw text — the payloads are already compact
 * JSON, and the DB stays the source of truth the clients re-read).
 *
 * DOWNSTREAM: browsers connect to /api/ai/events (server.ts's upgrade
 * handler hands the socket here). Each connection first receives a
 * bridge hello carrying the daemon connectivity, so a client can tell
 * "no runs happening" from "no channel" and lean on its poll fallback
 * while the daemon stream is down. Each browser socket is protocol-
 * pinged every 30s (isAlive) so one that died without a close frame is
 * reaped; forwarded daemon heartbeats keep the path warm meanwhile.
 * A page refresh simply closes the old socket (reaped by the pings if
 * the close frame is lost) and opens a fresh one with a fresh hello —
 * the renew is the browser's own reconnect, nothing to re-auth.
 */
import WebSocket from "ws";

/** The deep-agents FastAPI daemon's event stream endpoint. */
const DAEMON_WS_URL = "ws://127.0.0.1:8766/ws";

/**
 * Upstream liveness — the daemon sends an app-level {"type":"heartbeat"}
 * frame whenever no run event flowed for 20s (server.py WS_HEARTBEAT_SECONDS).
 * If OPEN but no frame of any kind arrived for longer than two missed
 * heartbeats plus margin, the connection is presumed dead half-open (a
 * daemon killed hard or a hung WSL forward fires no "close") and is
 * terminated so the reconnect path takes over. The checker polls at a
 * fraction of the stale threshold.
 */
const STALE_AFTER_MS = 45_000;
const STALE_CHECK_MS = 5_000;

/** Connected browser sockets (each /api/ai/events upgrade). */
const clients = new Set<WebSocket>();

let daemon: WebSocket | null = null;
let lastDaemonFrameAt = 0;
let staleTimer: ReturnType<typeof setInterval> | null = null;
let started = false;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelayMs = 1_000;

function daemonConnected(): boolean {
  return daemon !== null && daemon.readyState === WebSocket.OPEN;
}

function broadcast(text: string): void {
  for (const client of clients) {
    if (client.readyState === WebSocket.OPEN) client.send(text);
  }
}

function checkDaemonStale(): void {
  if (!daemonConnected()) return;
  const silentForMs = Date.now() - lastDaemonFrameAt;
  if (silentForMs <= STALE_AFTER_MS) return;
  console.warn(
    `[ai-events] daemon stream silent for ${Math.round(silentForMs / 1000)}s — ` +
    "presumed dead, reconnecting",
  );
  // Hard terminate (no close handshake — the peer is presumed gone);
  // the "close" handler resets the state and schedules the reconnect.
  daemon?.terminate();
}

function scheduleReconnect(): void {
  if (reconnectTimer !== null) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectDaemon();
  }, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, 15_000);
}

function connectDaemon(): void {
  if (daemon !== null) return;
  try {
    daemon = new WebSocket(DAEMON_WS_URL);
  } catch (err) {
    console.error("[ai-events] daemon stream connect failed:", err);
    daemon = null;
    scheduleReconnect();
    return;
  }
  daemon.on("open", () => {
    console.log(`[ai-events] daemon stream connected (${DAEMON_WS_URL})`);
    lastDaemonFrameAt = Date.now();
    reconnectDelayMs = 1_000;
  });
  daemon.on("message", (data: WebSocket.RawData) => {
    // Text frames only — the daemon encodes compact JSON strings.
    lastDaemonFrameAt = Date.now();
    broadcast(typeof data === "string" ? data : data.toString());
  });
  daemon.on("error", (err: Error) => {
    console.error("[ai-events] daemon stream error:", err.message);
  });
  daemon.on("close", () => {
    daemon = null;
    console.warn("[ai-events] daemon stream closed — reconnecting");
    scheduleReconnect();
  });
}

/** Boot wiring (server.ts, after listen): open the upstream daemon
 *  connection and start the staleness checker. Idempotent — safe
 *  against double boot hooks. */
export function startAiEventsBridge(): void {
  if (started) return;
  started = true;
  connectDaemon();
  staleTimer = setInterval(checkDaemonStale, STALE_CHECK_MS);
}

/** Shutdown wiring (server.ts): drop the daemon stream and every
 *  browser socket so a nodemon restart releases them all at once. */
export function stopAiEventsBridge(): void {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (staleTimer !== null) {
    clearInterval(staleTimer);
    staleTimer = null;
  }
  daemon?.removeAllListeners();
  daemon?.close();
  daemon = null;
  for (const client of clients) client.close();
  clients.clear();
}

/** Bridge-side synthetic event — broadcast to every browser client in
 *  the daemon frame's shape ({ type, ...identity fields, ts }). The
 *  daemon stream only carries run lifecycle; the bridge injects its own
 *  frame when a bridge action changes shared state (the run delete's
 *  "thread_deleted") so every open page re-reads instead of holding a
 *  settled thread's data forever. */
export function publishAiEvent(
  event: { type: string } & Record<string, unknown>,
): void {
  broadcast(JSON.stringify({ ts: new Date().toISOString(), ...event }));
}

/** One browser connection landed on /api/ai/events (server.ts's upgrade
 *  handler): hello frame, then the daemon fan-out until it closes.
 *
 *  Client→upstream relay: ONLY a validated probe frame
 *  ({"type":"ping","probe_id"} — the Thread Graph view's run heartbeat)
 *  is forwarded to the daemon, whose probe_id-echoing pong broadcasts
 *  back to every client (browsers match their own probe_id). With the
 *  upstream down, the probe is answered LOCALLY with ok:false so the
 *  page learns at once instead of waiting out its timeout. Every other
 *  client frame is ignored — the channel is one-way downstream by
 *  design.
 *
 *  Downstream liveness: a protocol ping every 30s with the isAlive
 *  handshake (the browser's ws stack answers pongs automatically) — a
 *  browser that died without a close frame (refresh race, laptop
 *  sleep) is terminated after one missed pong instead of accumulating
 *  as a dead fan-out target. The daemon's heartbeat frames flowing
 *  through also keep the path warm for intermediary idle timeouts. */
const CLIENT_PING_MS = 30_000;

export function attachAiEventsClient(client: WebSocket): void {
  clients.add(client);
  let isAlive = true;
  client.on("pong", () => {
    isAlive = true;
  });
  const pingTimer = setInterval(() => {
    if (!isAlive) {
      client.terminate();
      return;
    }
    isAlive = false;
    client.ping();
  }, CLIENT_PING_MS);
  const drop = (): void => {
    clearInterval(pingTimer);
    clients.delete(client);
  };
  client.on("close", drop);
  client.on("error", drop);
  client.on("message", (data: WebSocket.RawData) => {
    let payload: unknown;
    try {
      payload = JSON.parse(String(data));
    } catch {
      return;
    }
    if (payload === null || typeof payload !== "object") return;
    const frame = payload as { type?: unknown; probe_id?: unknown };
    if (frame.type !== "ping") return;
    if (typeof frame.probe_id !== "string" || frame.probe_id.length === 0
      || frame.probe_id.length > 64) return;
    if (daemonConnected() && daemon !== null) {
      daemon.send(String(data));
      return;
    }
    client.send(JSON.stringify({
      type: "pong",
      probe_id: frame.probe_id,
      ok: false,
      daemon: false,
      ts: new Date().toISOString(),
    }));
  });
  client.send(
    JSON.stringify({
      type: "hello",
      daemon: daemonConnected(),
      ts: new Date().toISOString(),
    }),
  );
}
