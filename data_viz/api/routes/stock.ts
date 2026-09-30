/**
 * Stock API routes.
 *
 * GET /api/stock?code=000001.SZ&start_date=&end_date=
 *   Returns daily OHLC + pct_change + PE for a single stock code.
 *   `code` accepts both suffixed ("000001.SZ") and bare ("000001") forms.
 *
 * GET /api/stock/themes
 *   Returns the two-level L1 sector → L2 industry → stocks taxonomy tree
 *   (SectorNode[]), precomputed by build_classification.py.
 *
 * GET /api/stock/combined?sector=&industry=&code=&page=&page_size=
 *   Paginated stocks filtered by L1 sector + L2 industry, with optional
 *   exact-code search.
 */
import { Router, type Request, type Response } from "express";
import {
  getStockDaily,
  getStockDividends,
  getStockMargin,
  getStockCore,
  listStockThemes,
  listStrategyThemes,
  getStocksCombined,
} from "../services/stock.service.js";

const router = Router();

/** GET /api/stock/themes — two-level L1 sector → L2 industry tree */
router.get("/themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listStockThemes(exchange));
  } catch (err) {
    console.error("[stock-baseline/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/stock/strategy-themes — parallel L1 strategy → L2 theme tree */
router.get("/strategy-themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listStrategyThemes(exchange));
  } catch (err) {
    console.error("[stock-baseline/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/stock/combined?sector=&industry=&code=&page=&page_size=
 *  Paginated stocks filtered by L1 sector + L2 industry. */
router.get("/combined", async (req: Request, res: Response) => {
  try {
    const query = {
      sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
      industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
      strategy: typeof req.query.strategy === "string" ? req.query.strategy : undefined,
      theme: typeof req.query.theme === "string" ? req.query.theme : undefined,
      code: typeof req.query.code === "string" ? req.query.code : undefined,
      exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      page:
        typeof req.query.page === "string"
          ? parseInt(req.query.page, 10)
          : undefined,
      page_size:
        typeof req.query.page_size === "string"
          ? parseInt(req.query.page_size, 10)
          : undefined,
    };
    res.json(await getStocksCombined(query));
  } catch (err) {
    console.error("[stock-baseline/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/stock/core?code= — granular core block (identity ⋈ basic_stats:
 *  OHLC + pct_change + PE/EPS + name). The REQUIRED block of the trend
 *  chart's parallel load; its rows define the chart's x-axis. */
router.get("/core", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const startDate =
      typeof req.query.start_date === "string" ? req.query.start_date : undefined;
    const endDate =
      typeof req.query.end_date === "string" ? req.query.end_date : undefined;
    res.json(await getStockCore(code, startDate, endDate));
  } catch (err) {
    console.error("[stock-baseline/core] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/stock/margin?code= — granular margin block (liquidity_margin:
 *  trading amount/shares + rz/rq balances). Best-effort overlay block. */
router.get("/margin", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const startDate =
      typeof req.query.start_date === "string" ? req.query.start_date : undefined;
    const endDate =
      typeof req.query.end_date === "string" ? req.query.end_date : undefined;
    res.json(await getStockMargin(code, startDate, endDate));
  } catch (err) {
    console.error("[stock-baseline/margin] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/stock/dividends?code= — granular dividend block (all events,
 *  never windowed). Best-effort event-marker block. */
router.get("/dividends", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json({ code, dividends: await getStockDividends(code) });
  } catch (err) {
    console.error("[stock-baseline/dividends] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/stock?code=000001.SZ — single-stock daily OHLC + PE */
router.get("/", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const startDate =
      typeof req.query.start_date === "string" ? req.query.start_date : undefined;
    const endDate =
      typeof req.query.end_date === "string" ? req.query.end_date : undefined;
    res.json(await getStockDaily(code, startDate, endDate));
  } catch (err) {
    console.error("[stock-baseline] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
