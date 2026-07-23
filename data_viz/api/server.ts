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
