/**
 * Express API server for the data_viz dashboard.
 * Queries the PostgreSQL database (stats schema) via the shared db service.
 * See services/db.service.ts for connection-pool setup and database/.env for config.
 */
import express, {
  type Request,
  type Response,
  type NextFunction,
} from "express";
import cors from "cors";
// gzip responses — the MA-Spread chart JSON alone is ~64 MB uncompressed;
// compression cuts the transfer through the vite proxy chain by ~5-10x.
// Level 1 over level 6 (default): the big payloads (MA-Spread, CFFEX
// options combined ~170MB) are served over localhost where bandwidth is
// free but CPU is not — level 1 halves the gzip CPU for ~30% larger
// bodies that the loopback absorbs instantly.
import compression from "compression";

import debtRoutes from "./routes/debt.js";
import optionsRoutes from "./routes/options.js";
import etfMarginRoutes from "./routes/etf-margin.js";
import indexRoutes from "./routes/index.js";
import secCompositionRoutes from "./routes/sec-composition.js";
import stockRoutes from "./routes/stock.js";
import secBoardRoutes from "./routes/sec-board.js";
import { router as futuresRoutes } from "./routes/futures.js";
import liveDataRoutes from "./routes/live-data.js";
import liveOptionsRoutes from "./routes/live-options.js";
import analysisRoutes from "./routes/analysis.js";
import analysisAdviceRoutes from "./routes/analysis-advice.js";
import secBaselineRoutes from "./routes/sec-baseline.js";
import strategyRoutes from "./routes/strategy.js";
import newsRoutes from "./routes/news.js";
import aiRoutes from "./routes/ai.js";
import cacheRoutes from "./routes/cache.js";
import monitorRoutes from "./routes/monitor.js";
import configRoutes from "./routes/config.js";

const app: express.Application = express();

app.use(cors());
app.use(compression({ level: 1 }));
app.use(express.json({ limit: "10mb" }));
app.use(express.urlencoded({ extended: true, limit: "10mb" }));

/**
 * API Routes
 */
app.use("/api/debt", debtRoutes);
app.use("/api/szse-options", optionsRoutes);
app.use("/api/etf-margin", etfMarginRoutes);
app.use("/api/index", indexRoutes);
app.use("/api/sec-composition", secCompositionRoutes);
app.use("/api/sec-board", secBoardRoutes);
app.use("/api/stock", stockRoutes);
app.use("/api/futures", futuresRoutes);
app.use("/api/live-data", liveDataRoutes);
app.use("/api/live-options", liveOptionsRoutes);
app.use("/api/analysis", analysisRoutes);
app.use("/api/analysis-advice", analysisAdviceRoutes);
app.use("/api/sec-baseline", secBaselineRoutes);
app.use("/api/strategy", strategyRoutes);
app.use("/api/news", newsRoutes);
app.use("/api/ai", aiRoutes);
app.use("/api/cache", cacheRoutes);
app.use("/api/monitor", monitorRoutes);
app.use("/api/config", configRoutes);

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
