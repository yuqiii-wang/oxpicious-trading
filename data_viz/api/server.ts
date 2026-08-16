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

server.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${PORT} is already in use. Stop the other process or set PORT env var.`);
  } else {
    console.error('Server error:', err);
  }
  process.exit(1);
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
