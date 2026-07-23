/**
 * Express API server for the data_viz dashboard.
 * Queries the PostgreSQL database (stats schema) via the shared db service.
 * See lib/db.ts for connection-pool setup and database/.env for config.
 */
import express, {
  type Request,
  type Response,
  type NextFunction,
} from "express";
import cors from "cors";

import debtBaselineRoutes from "./routes/debt-baseline.js";
import szseOptionsRoutes from "./routes/szse-options.js";
import etfMarginRoutes from "./routes/etf-margin.js";
import indexBaselineRoutes from "./routes/index-baseline.js";
import secCompositionRoutes from "./routes/sec-composition.js";
import cacheRoutes from "./routes/cache.js";

const app: express.Application = express();

app.use(cors());
app.use(express.json({ limit: "10mb" }));
app.use(express.urlencoded({ extended: true, limit: "10mb" }));

/**
 * API Routes
 */
app.use("/api/debt-baseline", debtBaselineRoutes);
app.use("/api/szse-options", szseOptionsRoutes);
app.use("/api/etf-margin", etfMarginRoutes);
app.use("/api/index-baseline", indexBaselineRoutes);
app.use("/api/sec-composition", secCompositionRoutes);
app.use("/api/cache", cacheRoutes);

/**
 * Health check
 */
app.use("/api/health", (_req: Request, res: Response) => {
  res.status(200).json({ success: true, message: "ok" });
});

/**
 * Error handler
 */
app.use((error: Error, _req: Request, res: Response, _next: NextFunction) => {
  console.error("[api] unhandled error:", error);
  res.status(500).json({ success: false, error: "Server internal error" });
});

/**
 * 404 handler
 */
app.use((_req: Request, res: Response) => {
  res.status(404).json({ success: false, error: "API not found" });
});

export default app;
