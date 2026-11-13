/**
 * Index API routes.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam, intParam } from "../lib/http.js";
import {
  listIndices,
  getIndexIntraday5min,
  listIndexThemes,
  listStrategyThemes,
  getIndicesCombined,
} from "../services/market/index.service.js";

const router = Router();

/** GET /api/index/list — list all available indices (flat list) */
router.get("/list", wrapAsync(async (_req: Request, res: Response) => {
  res.json(await listIndices());
}));

/** GET /api/index/themes — two-level L1 sector → L2 industry tree
 *  (LEFT column: industry-primary indices only). */
router.get("/themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listIndexThemes(exchange));
}));

/** GET /api/index/strategy-themes — parallel L1 strategy → L2 theme
 *  tree (RIGHT column: strategy-primary indices only). */
router.get("/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listStrategyThemes(exchange));
}));

/** GET /api/index/combined?sector=&industry=&strategy=&theme=&page=&page_size=
 *  Paginated indices filtered by L1 sector + L2 industry (LEFT column) OR
 *  L1 strategy + L2 theme (RIGHT column). When `strategy` is set (and
 *  `sector` is not), strategy/theme filtering applies. */
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
  res.json(await getIndicesCombined(query));
}));

/** GET /api/index/intraday-5min?code=000001&date=2026-07-21 */
router.get("/intraday-5min", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  const date = strParam(req, "date", { required: true });
  res.json(await getIndexIntraday5min(code, date));
}));

export default router;
