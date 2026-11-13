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
import { wrapAsync, strParam, intParam } from "../lib/http.js";
import {
  getStockDaily,
  getStockDividends,
  getStockMargin,
  getStockCore,
  listStockThemes,
  listStrategyThemes,
  getStocksCombined,
} from "../services/market/stock.service.js";

const router = Router();

/** GET /api/stock/themes — two-level L1 sector → L2 industry tree */
router.get("/themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listStockThemes(exchange));
}));

/** GET /api/stock/strategy-themes — parallel L1 strategy → L2 theme tree */
router.get("/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listStrategyThemes(exchange));
}));

/** GET /api/stock/combined?sector=&industry=&code=&page=&page_size=
 *  Paginated stocks filtered by L1 sector + L2 industry. */
router.get("/combined", wrapAsync(async (req: Request, res: Response) => {
  const query = {
    sector: strParam(req, "sector"),
    industry: strParam(req, "industry"),
    strategy: strParam(req, "strategy"),
    theme: strParam(req, "theme"),
    code: strParam(req, "code"),
    exchange: strParam(req, "exchange"),
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
    page: intParam(req, "page"),
    page_size: intParam(req, "page_size"),
  };
  res.json(await getStocksCombined(query));
}));

/** GET /api/stock/core?code= — granular core block (identity ⋈ basic_stats:
 *  OHLC + pct_change + PE/EPS + name). The REQUIRED block of the trend
 *  chart's parallel load; its rows define the chart's x-axis. */
router.get("/core", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  const startDate = strParam(req, "start_date");
  const endDate = strParam(req, "end_date");
  res.json(await getStockCore(code, startDate, endDate));
}));

/** GET /api/stock/margin?code= — granular margin block (liquidity_margin:
 *  trading amount/shares + rz/rq balances). Best-effort overlay block. */
router.get("/margin", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  const startDate = strParam(req, "start_date");
  const endDate = strParam(req, "end_date");
  res.json(await getStockMargin(code, startDate, endDate));
}));

/** GET /api/stock/dividends?code= — granular dividend block (all events,
 *  never windowed). Best-effort event-marker block. */
router.get("/dividends", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  res.json({ code, dividends: await getStockDividends(code) });
}));

/** GET /api/stock?code=000001.SZ — single-stock daily OHLC + PE */
router.get("/", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  const startDate = strParam(req, "start_date");
  const endDate = strParam(req, "end_date");
  res.json(await getStockDaily(code, startDate, endDate));
}));

export default router;
