/**
 * local server entry file, for local development
 */
import app from './app.js';
import { closePool } from './lib/db.js';

/**
 * start server with port
 */
const PORT = process.env.PORT || 3001;

const server = app.listen(PORT, () => {
  console.log(`Server ready on port ${PORT}`);
});

/**
 * EADDRINUSE retry — a nodemon restart can race the old instance's port
 * release; exiting here makes nodemon wait for a file change before
 * restarting, leaving the API down (every page gets bare-500s from the
 * vite proxy) until something is touched. Re-listen every 500ms for up
 * to 30s instead — the dying instance's port frees within seconds. A
 * port still held after 30s is a real conflict (second dev server) —
 * give up with the actionable message.
 */
const LISTEN_RETRY_MS = 500;
const LISTEN_RETRY_BUDGET_MS = 30_000;
let listenRetryDeadline: number | null = null;

server.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EADDRINUSE') {
    const now = Date.now();
    if (listenRetryDeadline == null) listenRetryDeadline = now + LISTEN_RETRY_BUDGET_MS;
    if (now < listenRetryDeadline) {
      console.warn(
        `Port ${PORT} busy — retrying listen in ${LISTEN_RETRY_MS}ms ` +
        `(up to ${LISTEN_RETRY_BUDGET_MS / 1000}s; likely the previous instance still releasing it)`,
      );
      setTimeout(() => server.listen(PORT), LISTEN_RETRY_MS);
      return;
    }
    console.error(
      `Port ${PORT} is still in use after ${LISTEN_RETRY_BUDGET_MS / 1000}s. ` +
      'Stop the other process or set PORT env var.',
    );
    process.exit(1);
  }
  console.error('Server error:', err);
  process.exit(1);
});

/**
 * Log unhandled promise rejections instead of dying on them — the default
 * (process crash) leaves nodemon waiting for a file change, and a crashed
 * API answers every UI request with the vite proxy's bare 500. Express 4
 * does not catch async route-handler rejections, so one bad handler would
 * otherwise take the whole API down.
 */
process.on('unhandledRejection', (reason) => {
  console.error('[api] unhandled promise rejection:', reason);
});

/**
 * close server + db pool
 */
async function shutdown(signal: string) {
  console.log(`${signal} signal received`);
  server.close(async () => {
    console.log('Server closed');
    await closePool();
    process.exit(0);
  });
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));

export default app;
